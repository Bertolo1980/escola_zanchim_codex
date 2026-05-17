from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AgendamentoLab, Aluno, Laboratorio, Professor, RegistroFaltaAluno, RegistroOcorrenciaAluno, Turma
from .services.whatsapp_service import (
    enviar_mensagem_whatsapp,
    enviar_template_aviso_falta_aluno,
    enviar_template_aviso_ocorrencia_aluno,
    formatar_numero_whatsapp,
)
from .views.faltas import _telefone_whatsapp_aluno


class FaltasOcorrenciasSeparacaoTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='digitador',
            password='senha-teste',
        )
        digitadores = Group.objects.create(name='Digitadores')
        self.user.groups.add(digitadores)

        self.turma = Turma.objects.create(
            nome='1A',
            ano=2026,
            serie='1 Ano',
            ativa=True,
            turno='manha',
        )
        self.aluno = Aluno.objects.create(
            nome='Aluno Teste',
            numero=7,
            turma=self.turma,
        )

    def test_falta_aluno_fica_apenas_em_registro_falta_aluno(self):
        falta = RegistroFaltaAluno.objects.create(
            aluno=self.aluno,
            data=date(2026, 5, 15),
            quantidade_faltas=1,
            pedagoga='Sonia',
            registrado_por=self.user,
        )

        self.assertTrue(RegistroFaltaAluno.objects.filter(pk=falta.pk).exists())
        self.assertFalse(
            RegistroOcorrenciaAluno.objects.filter(
                aluno=self.aluno,
                data=falta.data,
            ).exists()
        )

    def test_ocorrencia_aluno_fica_apenas_em_registro_ocorrencia_aluno(self):
        ocorrencia = RegistroOcorrenciaAluno.objects.create(
            aluno=self.aluno,
            data=date(2026, 5, 15),
            tipo_ocorrencia='atraso',
            faltou=False,
            turno='manha',
            atendido_por='Sonia',
            registrado_por=self.user,
        )

        self.assertTrue(RegistroOcorrenciaAluno.objects.filter(pk=ocorrencia.pk).exists())
        self.assertFalse(
            RegistroFaltaAluno.objects.filter(
                aluno=self.aluno,
                data=ocorrencia.data,
            ).exists()
        )

    def test_fluxo_ocorrencia_sem_tipo_valido_nao_cai_no_default_falta(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('formulario_digitador'),
            {
                'turma': self.turma.pk,
                'numero_aluno': self.aluno.numero,
                'nome_aluno': self.aluno.nome,
                'data': '2026-05-15',
                'turno': 'manha',
                'faltou': '',
                'tipo_ocorrencia': 'falta',
                'motivo_alegado': 'Teste de tipo invalido',
                'atendido_por': 'Sonia',
                'responsavel_contatado': '',
                'horario_chegada': '',
                'horario_contato': '',
                'alegado_responsavel': '',
            },
        )

        self.assertRedirects(response, reverse('formulario_digitador'))
        ocorrencia = RegistroOcorrenciaAluno.objects.get(aluno=self.aluno)
        self.assertEqual(ocorrencia.tipo_ocorrencia, 'atraso')
        self.assertFalse(ocorrencia.faltou)
        self.assertFalse(
            RegistroFaltaAluno.objects.filter(
                aluno=self.aluno,
                data=ocorrencia.data,
            ).exists()
        )

    def _post_registrar_falta(self, possui_atestado='nao', follow=True):
        self.client.force_login(self.user)
        return self.client.post(
            reverse('registrar_falta_aluno'),
            {
                'aluno': self.aluno.pk,
                'data': '2026-05-15',
                'quantidade': '1',
                'pedagoga': 'Sonia',
                'possui_atestado': possui_atestado,
                'observacoes': 'Teste de aviso',
            },
            follow=follow,
        )

    def _post_registrar_ocorrencia(self, tipo_ocorrencia='piercing', confirmar_reenvio=False, follow=True):
        self.client.force_login(self.user)
        dados = {
            'turma': self.turma.pk,
            'numero_aluno': self.aluno.numero,
            'nome_aluno': self.aluno.nome,
            'data': '2026-05-15',
            'turno': 'manha',
            'faltou': '',
            'tipo_ocorrencia': tipo_ocorrencia,
            'motivo_alegado': 'Teste de ocorrencia',
            'atendido_por': 'Sonia',
            'pedagoga': 'Sonia',
            'responsavel_contatado': '',
            'horario_chegada': '',
            'horario_contato': '',
            'alegado_responsavel': '',
            'observacoes_adicionais': '',
        }
        if confirmar_reenvio:
            dados['confirmar_reenvio_whatsapp'] = '1'
        return self.client.post(
            reverse('registrar_ocorrencia_aluno'),
            dados,
            follow=follow,
        )

    @patch('apps.views.faltas.enviar_template_aviso_falta_aluno')
    def test_falta_com_atestado_nao_envia_whatsapp(self, enviar_mock):
        response = self._post_registrar_falta(possui_atestado='sim')

        falta = RegistroFaltaAluno.objects.get(aluno=self.aluno)
        self.assertTrue(falta.justificada)
        enviar_mock.assert_not_called()
        self.assertContains(response, 'Falta registrada com atestado. WhatsApp nao enviado.')

    @patch('apps.views.faltas.enviar_template_aviso_falta_aluno')
    def test_falta_sem_telefone_salva_e_registra_alerta_sem_envio(self, enviar_mock):
        with self.assertLogs('apps.views.faltas', level='WARNING') as logs:
            response = self._post_registrar_falta(possui_atestado='nao')

        self.assertTrue(RegistroFaltaAluno.objects.filter(aluno=self.aluno).exists())
        enviar_mock.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Falta registrada, mas nao ha telefone cadastrado para envio.')
        self.assertIn('aluno sem telefone cadastrado', '\n'.join(logs.output))

    def test_telefone_whatsapp_usa_telefone_quando_responsavel_vazio(self):
        aluno = SimpleNamespace(telefone_responsavel='   ', telefone='5544999501967')

        self.assertEqual(_telefone_whatsapp_aluno(aluno), '5544999501967')

    @patch('apps.views.faltas.enviar_template_aviso_falta_aluno')
    def test_falta_com_falha_no_whatsapp_salva_e_registra_log(self, enviar_mock):
        self.aluno.telefone = '(44) 99999-0000'
        self.aluno.save(update_fields=['telefone'])
        enviar_mock.return_value = {
            'status': False,
            'numero': '5544999990000',
            'erro': {'message': 'Numero invalido'},
            'resposta': {'error': {'message': 'Numero invalido'}},
            'status_code': 400,
        }

        with self.assertLogs('apps.views.faltas', level='WARNING') as logs:
            response = self._post_registrar_falta(possui_atestado='nao')

        self.assertTrue(RegistroFaltaAluno.objects.filter(aluno=self.aluno).exists())
        enviar_mock.assert_called_once()
        self.assertEqual(enviar_mock.call_args.args[0], '(44) 99999-0000')
        self.assertEqual(enviar_mock.call_args.args[1], self.aluno.nome)
        self.assertEqual(enviar_mock.call_args.args[2], '15/05/2026')
        self.assertContains(response, 'Falta registrada, mas o aviso nao foi enviado. Confira o numero do responsavel.')
        self.assertIn('envio de WhatsApp falhou', '\n'.join(logs.output))

    @patch('apps.views.faltas.enviar_template_aviso_falta_aluno')
    def test_falta_com_whatsapp_enviado_exibe_sucesso(self, enviar_mock):
        self.aluno.telefone = '(44) 99999-0000'
        self.aluno.save(update_fields=['telefone'])
        enviar_mock.return_value = {
            'status': True,
            'numero': '5544999990000',
            'resposta': {'messages': [{'id': 'wamid.teste'}]},
            'erro': None,
            'status_code': 200,
        }

        response = self._post_registrar_falta(possui_atestado='nao')

        self.assertTrue(RegistroFaltaAluno.objects.filter(aluno=self.aluno).exists())
        enviar_mock.assert_called_once()
        self.assertEqual(enviar_mock.call_args.args[0], '(44) 99999-0000')
        self.assertEqual(enviar_mock.call_args.args[1], self.aluno.nome)
        self.assertEqual(enviar_mock.call_args.args[2], '15/05/2026')
        self.assertContains(response, 'Falta registrada e aviso enviado pelo WhatsApp.')

    @patch('apps.views.alunos.enviar_template_aviso_ocorrencia_aluno')
    def test_piercing_primeira_vez_envia_whatsapp(self, enviar_mock):
        self.aluno.telefone = '5544999501967'
        self.aluno.save(update_fields=['telefone'])
        enviar_mock.return_value = {
            'status': True,
            'numero': '5544999501967',
            'resposta': {'messages': [{'id': 'wamid.teste'}]},
            'erro': None,
            'status_code': 200,
        }

        response = self._post_registrar_ocorrencia(tipo_ocorrencia='piercing')

        self.assertTrue(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, tipo_ocorrencia='piercing').exists())
        enviar_mock.assert_called_once()
        self.assertEqual(enviar_mock.call_args.args[0], '5544999501967')
        self.assertEqual(enviar_mock.call_args.args[1], self.aluno.nome)
        self.assertEqual(enviar_mock.call_args.args[2], 'piercing')
        self.assertEqual(enviar_mock.call_args.args[3], '15/05/2026')
        self.assertContains(response, 'Ocorrencia registrada e aviso enviado pelo WhatsApp.')

    @patch('apps.views.alunos.enviar_template_aviso_ocorrencia_aluno')
    def test_segunda_ocorrencia_diferente_no_mesmo_dia_nao_envia_automatico(self, enviar_mock):
        RegistroOcorrenciaAluno.objects.create(
            aluno=self.aluno,
            data=date(2026, 5, 15),
            tipo_ocorrencia='atraso',
            faltou=False,
            turno='manha',
            atendido_por='Sonia',
            registrado_por=self.user,
        )
        self.aluno.telefone = '5544999501967'
        self.aluno.save(update_fields=['telefone'])

        response = self._post_registrar_ocorrencia(tipo_ocorrencia='piercing')

        self.assertEqual(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, data=date(2026, 5, 15)).count(), 2)
        enviar_mock.assert_not_called()
        self.assertContains(response, 'Ja existe outra ocorrencia para este aluno nesta data. WhatsApp nao enviado automaticamente.')

    @patch('apps.views.alunos.enviar_template_aviso_ocorrencia_aluno')
    def test_piercing_segunda_vez_no_mesmo_dia_nao_envia_sem_confirmacao(self, enviar_mock):
        RegistroOcorrenciaAluno.objects.create(
            aluno=self.aluno,
            data=date(2026, 5, 15),
            tipo_ocorrencia='piercing',
            faltou=False,
            turno='manha',
            atendido_por='Sonia',
            registrado_por=self.user,
        )
        self.aluno.telefone = '5544999501967'
        self.aluno.save(update_fields=['telefone'])

        response = self._post_registrar_ocorrencia(tipo_ocorrencia='piercing')

        self.assertEqual(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, data=date(2026, 5, 15)).count(), 2)
        self.assertTrue(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, tipo_ocorrencia='piercing_r2').exists())
        enviar_mock.assert_not_called()
        self.assertContains(response, 'Esta e a segunda ocorrencia deste tipo hoje. Deseja enviar novo aviso pelo WhatsApp?')

    @patch('apps.views.alunos.enviar_template_aviso_ocorrencia_aluno')
    def test_piercing_segunda_vez_com_confirmacao_envia_whatsapp(self, enviar_mock):
        RegistroOcorrenciaAluno.objects.create(
            aluno=self.aluno,
            data=date(2026, 5, 15),
            tipo_ocorrencia='piercing',
            faltou=False,
            turno='manha',
            atendido_por='Sonia',
            registrado_por=self.user,
        )
        self.aluno.telefone = '5544999501967'
        self.aluno.save(update_fields=['telefone'])
        enviar_mock.return_value = {
            'status': True,
            'numero': '5544999501967',
            'resposta': {'messages': [{'id': 'wamid.teste'}]},
            'erro': None,
            'status_code': 200,
        }

        response = self._post_registrar_ocorrencia(tipo_ocorrencia='piercing', confirmar_reenvio=True)

        self.assertEqual(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, data=date(2026, 5, 15)).count(), 2)
        self.assertTrue(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, tipo_ocorrencia='piercing_r2').exists())
        enviar_mock.assert_called_once()
        self.assertEqual(enviar_mock.call_args.args[2], 'piercing')
        self.assertContains(response, 'Ocorrencia registrada e aviso enviado pelo WhatsApp.')

    @patch('apps.views.alunos.enviar_template_aviso_ocorrencia_aluno')
    def test_uniforme_segunda_vez_no_mesmo_dia_usa_regra_de_confirmacao(self, enviar_mock):
        RegistroOcorrenciaAluno.objects.create(
            aluno=self.aluno,
            data=date(2026, 5, 15),
            tipo_ocorrencia='uniforme',
            faltou=False,
            turno='manha',
            atendido_por='Sonia',
            registrado_por=self.user,
        )
        self.aluno.telefone = '5544999501967'
        self.aluno.save(update_fields=['telefone'])

        response = self._post_registrar_ocorrencia(tipo_ocorrencia='uniforme')

        self.assertEqual(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, data=date(2026, 5, 15)).count(), 2)
        self.assertTrue(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, tipo_ocorrencia='uniforme_r2').exists())
        enviar_mock.assert_not_called()
        self.assertContains(response, 'Esta e a segunda ocorrencia deste tipo hoje. Deseja enviar novo aviso pelo WhatsApp?')

    @patch('apps.views.alunos.enviar_template_aviso_ocorrencia_aluno')
    def test_fora_de_sala_primeira_vez_envia_whatsapp(self, enviar_mock):
        self.aluno.telefone = '5544999501967'
        self.aluno.save(update_fields=['telefone'])
        enviar_mock.return_value = {
            'status': True,
            'numero': '5544999501967',
            'resposta': {'messages': [{'id': 'wamid.teste'}]},
            'erro': None,
            'status_code': 200,
        }

        response = self._post_registrar_ocorrencia(tipo_ocorrencia='fora_sala')

        self.assertTrue(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, tipo_ocorrencia='fora_sala').exists())
        enviar_mock.assert_called_once()
        self.assertEqual(enviar_mock.call_args.args[2], 'fora de sala sem autorizacao')
        self.assertContains(response, 'Ocorrencia registrada e aviso enviado pelo WhatsApp.')

    @patch('apps.views.alunos.enviar_template_aviso_ocorrencia_aluno')
    def test_matando_aula_primeira_vez_envia_whatsapp(self, enviar_mock):
        self.aluno.telefone = '5544999501967'
        self.aluno.save(update_fields=['telefone'])
        enviar_mock.return_value = {
            'status': True,
            'numero': '5544999501967',
            'resposta': {'messages': [{'id': 'wamid.teste'}]},
            'erro': None,
            'status_code': 200,
        }

        response = self._post_registrar_ocorrencia(tipo_ocorrencia='matando_aula')

        self.assertTrue(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, tipo_ocorrencia='matando_aula').exists())
        enviar_mock.assert_called_once()
        self.assertEqual(enviar_mock.call_args.args[2], 'matando aula')
        self.assertContains(response, 'Ocorrencia registrada e aviso enviado pelo WhatsApp.')

    @patch('apps.views.alunos.enviar_template_aviso_ocorrencia_aluno')
    def test_ocorrencia_tipo_falta_nao_envia_whatsapp(self, enviar_mock):
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory
        from apps.views.alunos import _notificar_ocorrencia_whatsapp

        ocorrencia = RegistroOcorrenciaAluno.objects.create(
            aluno=self.aluno,
            data=date(2026, 5, 15),
            tipo_ocorrencia='falta',
            faltou=False,
            turno='manha',
            atendido_por='Sonia',
            registrado_por=self.user,
        )
        request = RequestFactory().get('/')
        request.session = {}
        setattr(request, '_messages', FallbackStorage(request))

        _notificar_ocorrencia_whatsapp(request, ocorrencia)

        enviar_mock.assert_not_called()

    @patch('apps.views.alunos.enviar_template_aviso_ocorrencia_aluno')
    def test_ocorrencia_sem_telefone_nao_tenta_enviar(self, enviar_mock):
        with self.assertLogs('apps.views.alunos', level='WARNING') as logs:
            response = self._post_registrar_ocorrencia(tipo_ocorrencia='piercing')

        self.assertTrue(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, tipo_ocorrencia='piercing').exists())
        enviar_mock.assert_not_called()
        self.assertContains(response, 'Ocorrencia registrada, mas confira o telefone do responsavel.')
        self.assertIn('aluno sem telefone cadastrado', '\n'.join(logs.output))

    @patch('apps.views.alunos.enviar_template_aviso_ocorrencia_aluno')
    def test_ocorrencia_com_falha_no_whatsapp_salva_e_alerta(self, enviar_mock):
        self.aluno.telefone = '5544999501967'
        self.aluno.save(update_fields=['telefone'])
        enviar_mock.return_value = {
            'status': False,
            'numero': '5544999501967',
            'erro': {'message': 'Erro da API'},
            'resposta': {'error': {'message': 'Erro da API'}},
            'status_code': 400,
        }

        with self.assertLogs('apps.views.alunos', level='WARNING') as logs:
            response = self._post_registrar_ocorrencia(tipo_ocorrencia='piercing')

        self.assertTrue(RegistroOcorrenciaAluno.objects.filter(aluno=self.aluno, tipo_ocorrencia='piercing').exists())
        enviar_mock.assert_called_once()
        self.assertEqual(enviar_mock.call_args.args[0], '5544999501967')
        self.assertContains(response, 'Ocorrencia registrada, mas o aviso nao foi enviado. Confira o numero do responsavel.')
        self.assertIn('envio de WhatsApp falhou', '\n'.join(logs.output))


class WhatsAppServiceTests(TestCase):
    def test_formatar_numero_whatsapp_adiciona_codigo_do_brasil(self):
        self.assertEqual(formatar_numero_whatsapp('(44) 99999-0000'), '5544999990000')

    @override_settings(WHATSAPP_TOKEN='', PHONE_NUMBER_ID='')
    def test_enviar_mensagem_whatsapp_sem_configuracao_retorna_erro_controlado(self):
        resultado = enviar_mensagem_whatsapp('(44) 99999-0000', 'Mensagem de teste')

        self.assertFalse(resultado['status'])
        self.assertEqual(resultado['numero'], '5544999990000')
        self.assertIn('WHATSAPP_TOKEN', resultado['erro'])

    @override_settings(
        WHATSAPP_TOKEN='token-teste',
        PHONE_NUMBER_ID='123456',
        WHATSAPP_API_VERSION='v20.0',
    )
    @patch('apps.services.whatsapp_service.requests.post')
    def test_enviar_template_aviso_falta_aluno_usa_template_aprovado(self, post_mock):
        post_mock.return_value.ok = True
        post_mock.return_value.status_code = 200
        post_mock.return_value.json.return_value = {'messages': [{'id': 'wamid.teste'}]}

        resultado = enviar_template_aviso_falta_aluno('44999990000', 'Aluno Teste', '15/05/2026')

        self.assertTrue(resultado['status'])
        payload = post_mock.call_args.kwargs['json']
        self.assertEqual(payload['type'], 'template')
        self.assertEqual(payload['template']['name'], 'aviso_falta_aluno')
        self.assertEqual(payload['template']['language']['code'], 'pt_BR')
        parametros = payload['template']['components'][0]['parameters']
        self.assertEqual(parametros[0]['text'], 'Aluno Teste')
        self.assertEqual(parametros[1]['text'], '15/05/2026')

    @override_settings(
        WHATSAPP_TOKEN='token-teste',
        PHONE_NUMBER_ID='123456',
        WHATSAPP_API_VERSION='v20.0',
    )
    @patch('apps.services.whatsapp_service.requests.post')
    def test_enviar_template_aviso_ocorrencia_aluno_usa_template_aprovado(self, post_mock):
        post_mock.return_value.ok = True
        post_mock.return_value.status_code = 200
        post_mock.return_value.json.return_value = {'messages': [{'id': 'wamid.teste'}]}

        resultado = enviar_template_aviso_ocorrencia_aluno(
            '44999990000',
            'Aluno Teste',
            'piercing',
            '15/05/2026',
        )

        self.assertTrue(resultado['status'])
        payload = post_mock.call_args.kwargs['json']
        self.assertEqual(payload['type'], 'template')
        self.assertEqual(payload['template']['name'], 'aviso_ocorrencia_aluno')
        self.assertEqual(payload['template']['language']['code'], 'pt_BR')
        parametros = payload['template']['components'][0]['parameters']
        self.assertEqual(parametros[0]['text'], 'Aluno Teste')
        self.assertEqual(parametros[1]['text'], 'piercing')
        self.assertEqual(parametros[2]['text'], '15/05/2026')


class TesteProducaoSeguroCommandTests(TestCase):
    def setUp(self):
        self.turma = Turma.objects.create(
            nome='1A',
            ano=2026,
            serie='1 Ano',
            ativa=True,
            turno='manha',
        )
        Aluno.objects.create(
            nome='Aluno Teste',
            numero=1,
            turma=self.turma,
            telefone='5544999501967',
        )

    @override_settings(
        WHATSAPP_TOKEN='token-teste',
        PHONE_NUMBER_ID='123456',
        WHATSAPP_API_VERSION='v20.0',
    )
    def test_teste_producao_seguro_executa_sem_alterar_banco(self):
        from io import StringIO

        alunos_antes = Aluno.objects.count()
        turmas_antes = Turma.objects.count()
        ocorrencias_antes = RegistroOcorrenciaAluno.objects.count()
        faltas_antes = RegistroFaltaAluno.objects.count()
        out = StringIO()

        call_command('teste_producao_seguro', stdout=out)

        saida = out.getvalue()
        self.assertIn('OK - Django check executado sem erros.', saida)
        self.assertIn('OK - Variaveis WhatsApp configuradas.', saida)
        self.assertIn('OK - Import apps.views.alunos.', saida)
        self.assertIn('OK - Template ocorrencias/registrar_ocorrencia.html renderizou sem erro.', saida)
        self.assertEqual(Aluno.objects.count(), alunos_antes)
        self.assertEqual(Turma.objects.count(), turmas_antes)
        self.assertEqual(RegistroOcorrenciaAluno.objects.count(), ocorrencias_antes)
        self.assertEqual(RegistroFaltaAluno.objects.count(), faltas_antes)

    @override_settings(
        WHATSAPP_TOKEN='',
        PHONE_NUMBER_ID='',
        WHATSAPP_API_VERSION='',
    )
    def test_teste_producao_seguro_falha_quando_whatsapp_nao_configurado(self):
        from io import StringIO

        out = StringIO()

        with self.assertRaises(CommandError):
            call_command('teste_producao_seguro', stdout=out)

        self.assertIn('FALHA - Variaveis WhatsApp ausentes', out.getvalue())


class LaboratoriosCronogramaTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='direcao',
            password='senha-teste',
            is_superuser=True,
        )
        self.professor_user = User.objects.create_user(
            username='professor',
            password='senha-teste',
        )
        self.professor = Professor.objects.create(
            usuario=self.professor_user,
            nome_completo='Professor Teste',
            ativo=True,
        )
        self.outro_professor = Professor.objects.create(nome_completo='Outro Professor', ativo=True)
        self.terceiro_professor = Professor.objects.create(nome_completo='Terceiro Professor', ativo=True)
        self.turma = Turma.objects.create(
            nome='2A',
            ano=2026,
            serie='2 Ano',
            ativa=True,
            turno='manha',
        )
        self.lab1 = Laboratorio.objects.create(
            nome='Laboratorio 1',
            tipo='fixo',
            equipamento='Computadores',
            ativo=True,
        )
        self.lab2 = Laboratorio.objects.create(
            nome='Laboratorio 2',
            tipo='fixo',
            equipamento='Computadores',
            ativo=True,
        )

    def test_agendamento_salva_laboratorio_selecionado_no_formulario(self):
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('agendamento_lab', args=[self.lab1.id]),
            {
                'laboratorio': self.lab2.id,
                'data': '2026-05-15',
                'horario': '1',
                'turno': 'manha',
                'professor': self.professor.id,
                'disciplina': 'Matematica',
                'turma': self.turma.id,
                'observacao': '',
            },
        )

        self.assertRedirects(response, reverse('listar_laboratorios'))
        agendamento = AgendamentoLab.objects.get()
        self.assertEqual(agendamento.laboratorio, self.lab2)

    def test_cronograma_abre_na_semana_mais_recente_e_lista_por_data_e_id_desc(self):
        self.client.force_login(self.user)
        antigo = AgendamentoLab.objects.create(
            laboratorio=self.lab1,
            data=date(2026, 5, 11),
            horario='1',
            turno='manha',
            professor=self.professor,
            disciplina='Historia',
            turma=self.turma,
            registrado_por=self.user,
        )
        recente_menor_id = AgendamentoLab.objects.create(
            laboratorio=self.lab2,
            data=date(2026, 5, 20),
            horario='2',
            turno='manha',
            professor=self.professor,
            disciplina='Ciencias',
            turma=self.turma,
            registrado_por=self.user,
        )
        recente_maior_id = AgendamentoLab.objects.create(
            laboratorio=self.lab1,
            data=date(2026, 5, 20),
            horario='3',
            turno='manha',
            professor=self.professor,
            disciplina='Geografia',
            turma=self.turma,
            registrado_por=self.user,
        )

        response = self.client.get(reverse('cronograma_semanal'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['data_inicio'], date(2026, 5, 18))
        self.assertEqual(list(response.context['agendamentos_recentes']), [recente_maior_id, recente_menor_id])

        response = self.client.get(reverse('cronograma_semanal'), {'data_inicio': '2026-05-11'})

        self.assertEqual(list(response.context['agendamentos_recentes']), [antigo])

    def test_cronograma_mostra_todos_professores_separados_por_laboratorio(self):
        agendamento_manha = AgendamentoLab.objects.create(
            laboratorio=self.lab1,
            data=date(2026, 5, 18),
            horario='1',
            turno='manha',
            professor=self.professor,
            disciplina='Matematica',
            turma=self.turma,
            registrado_por=self.user,
        )
        agendamento_lab2 = AgendamentoLab.objects.create(
            laboratorio=self.lab2,
            data=date(2026, 5, 19),
            horario='2',
            turno='manha',
            professor=self.outro_professor,
            disciplina='Portugues',
            turma=self.turma,
            registrado_por=self.user,
        )
        agendamento_tarde = AgendamentoLab.objects.create(
            laboratorio=self.lab1,
            data=date(2026, 5, 18),
            horario='1',
            turno='tarde',
            professor=self.terceiro_professor,
            disciplina='Arte',
            turma=self.turma,
            registrado_por=self.user,
        )
        self.client.force_login(self.professor_user)

        response = self.client.get(reverse('cronograma_semanal'), {'data_inicio': '2026-05-18'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(AgendamentoLab.objects.count(), 3)
        self.assertEqual(list(response.context['agendamentos_recentes']), list(
            AgendamentoLab.objects.select_related('laboratorio', 'professor', 'turma').order_by('-data', '-id')
        ))

        secoes = response.context['cronograma_por_laboratorio']
        self.assertEqual([secao['laboratorio'] for secao in secoes], [self.lab1, self.lab2])
        celula_lab1_primeira_aula = secoes[0]['linhas'][0]['dias'][0]['agendamentos']
        self.assertEqual(celula_lab1_primeira_aula, [agendamento_tarde, agendamento_manha])
        self.assertContains(response, 'Matematica')
        self.assertContains(response, 'Portugues')
        self.assertContains(response, 'Arte')
        self.assertContains(response, 'Professor Teste')
        self.assertContains(response, 'Outro Professor')
        self.assertContains(response, 'Terceiro Professor')
        self.assertContains(response, self.lab1.nome)
        self.assertContains(response, self.lab2.nome)

    def test_cronograma_filtro_laboratorio_e_opcional(self):
        AgendamentoLab.objects.create(
            laboratorio=self.lab1,
            data=date(2026, 5, 18),
            horario='1',
            turno='manha',
            professor=self.professor,
            disciplina='Matematica',
            turma=self.turma,
            registrado_por=self.user,
        )
        AgendamentoLab.objects.create(
            laboratorio=self.lab2,
            data=date(2026, 5, 19),
            horario='2',
            turno='manha',
            professor=self.outro_professor,
            disciplina='Portugues',
            turma=self.turma,
            registrado_por=self.user,
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse('cronograma_semanal'),
            {'data_inicio': '2026-05-18', 'laboratorio': str(self.lab2.id)},
        )

        self.assertEqual([secao['laboratorio'] for secao in response.context['cronograma_por_laboratorio']], [self.lab2])
        self.assertEqual(list(response.context['agendamentos_recentes']), [AgendamentoLab.objects.get(laboratorio=self.lab2)])
