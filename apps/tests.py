from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Aluno, RegistroFaltaAluno, RegistroOcorrenciaAluno, Turma
from .services.whatsapp_service import enviar_mensagem_whatsapp, enviar_template_aviso_falta_aluno, formatar_numero_whatsapp
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

    @patch('apps.views.faltas.enviar_template_aviso_falta_aluno')
    def test_falta_com_atestado_nao_envia_whatsapp(self, enviar_mock):
        response = self._post_registrar_falta(possui_atestado='sim')

        falta = RegistroFaltaAluno.objects.get(aluno=self.aluno)
        self.assertTrue(falta.justificada)
        enviar_mock.assert_not_called()
        self.assertContains(response, 'Falta registrada com atestado e sem envio de aviso.')

    @patch('apps.views.faltas.enviar_template_aviso_falta_aluno')
    def test_falta_sem_telefone_salva_e_registra_alerta_sem_envio(self, enviar_mock):
        with self.assertLogs('apps.views.faltas', level='WARNING') as logs:
            response = self._post_registrar_falta(possui_atestado='nao')

        self.assertTrue(RegistroFaltaAluno.objects.filter(aluno=self.aluno).exists())
        enviar_mock.assert_not_called()
        self.assertEqual(response.status_code, 200)
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
        self.assertEqual(response.status_code, 200)
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
