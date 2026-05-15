from datetime import date

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.urls import reverse

from .models import Aluno, RegistroFaltaAluno, RegistroOcorrenciaAluno, Turma


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
