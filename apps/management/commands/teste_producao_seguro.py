from datetime import date, timedelta
from importlib import import_module
from io import StringIO

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.template.loader import render_to_string
from django.test import RequestFactory

from apps.forms import RegistroOcorrenciaForm
from apps.models import Aluno, Laboratorio, Turma


class Command(BaseCommand):
    help = 'Executa validacoes seguras de producao sem enviar WhatsApp ou alterar o banco.'

    def handle(self, *args, **options):
        self.falhas = 0
        self.avisos = 0

        self.stdout.write('Relatorio de teste seguro de producao')
        self.stdout.write('Nenhum WhatsApp sera enviado e nenhum dado sera criado ou alterado.')
        self.stdout.write('')

        self._validar_django_check()
        self._validar_configuracao_whatsapp()
        self._validar_imports()
        self._validar_dados_basicos()
        self._validar_templates()

        self.stdout.write('')
        if self.falhas:
            self.stdout.write(self.style.ERROR(f'FALHA: {self.falhas} item(ns) precisam de correcao.'))
            if self.avisos:
                self.stdout.write(self.style.WARNING(f'AVISO: {self.avisos} item(ns) merecem atencao.'))
            raise CommandError('Teste seguro de producao encontrou falhas.')

        self.stdout.write(self.style.SUCCESS('OK: teste seguro de producao concluido sem falhas.'))
        if self.avisos:
            self.stdout.write(self.style.WARNING(f'AVISO: {self.avisos} item(ns) merecem atencao.'))

    def _ok(self, mensagem):
        self.stdout.write(self.style.SUCCESS(f'OK - {mensagem}'))

    def _falha(self, mensagem):
        self.falhas += 1
        self.stdout.write(self.style.ERROR(f'FALHA - {mensagem}'))

    def _aviso(self, mensagem):
        self.avisos += 1
        self.stdout.write(self.style.WARNING(f'AVISO - {mensagem}'))

    def _validar_django_check(self):
        output = StringIO()
        try:
            call_command('check', stdout=output, stderr=output)
        except Exception as exc:
            self._falha(f'Django check encontrou problema: {exc}')
            detalhe = output.getvalue().strip()
            if detalhe:
                self.stdout.write(detalhe)
            return
        self._ok('Django check executado sem erros.')

    def _validar_configuracao_whatsapp(self):
        variaveis = ('WHATSAPP_TOKEN', 'PHONE_NUMBER_ID', 'WHATSAPP_API_VERSION')
        faltando = [nome for nome in variaveis if not str(getattr(settings, nome, '') or '').strip()]
        if faltando:
            self._falha(f'Variaveis WhatsApp ausentes: {", ".join(faltando)}.')
            return
        self._ok('Variaveis WhatsApp configuradas.')

    def _validar_imports(self):
        modulos = (
            'apps.services.whatsapp_service',
            'apps.views.alunos',
            'apps.views.faltas',
            'apps.views.laboratorios',
        )
        for modulo in modulos:
            try:
                import_module(modulo)
            except Exception as exc:
                self._falha(f'Nao foi possivel importar {modulo}: {exc}')
            else:
                self._ok(f'Import {modulo}.')

    def _validar_dados_basicos(self):
        if Aluno.objects.exists():
            self._ok('Existem alunos cadastrados.')
        else:
            self._falha('Nao existem alunos cadastrados.')

        if Turma.objects.exists():
            self._ok('Existem turmas cadastradas.')
        else:
            self._falha('Nao existem turmas cadastradas.')

        filtro_telefone = Q()
        campos_aluno = {campo.name for campo in Aluno._meta.get_fields()}
        if 'telefone_responsavel' in campos_aluno:
            filtro_telefone |= Q(telefone_responsavel__isnull=False) & ~Q(telefone_responsavel='')
        if 'telefone' in campos_aluno:
            filtro_telefone |= Q(telefone__isnull=False) & ~Q(telefone='')

        if filtro_telefone and Aluno.objects.filter(filtro_telefone).exists():
            self._ok('Existe ao menos um aluno com telefone cadastrado.')
        else:
            self._falha('Nao existe aluno com telefone cadastrado.')

    def _validar_templates(self):
        factory = RequestFactory()
        request = factory.get('/teste-producao-seguro/')
        request.user = AnonymousUser()

        templates = (
            (
                'laboratorios/cronograma.html',
                self._contexto_cronograma_template(),
            ),
            (
                'ocorrencias/formulario_digitador.html',
                {
                    'form': RegistroOcorrenciaForm(),
                    'ultimas_ocorrencias': [],
                },
            ),
            (
                'ocorrencias/registrar_ocorrencia.html',
                {
                    'form': RegistroOcorrenciaForm(),
                    'ultimas_ocorrencias': [],
                },
            ),
            (
                'faltas/registrar_falta_aluno.html',
                {
                    'turmas': [],
                    'alunos': [],
                },
            ),
            (
                'laboratorios/listar.html',
                {
                    'laboratorios': Laboratorio.objects.none(),
                },
            ),
        )

        for template_name, contexto in templates:
            try:
                render_to_string(template_name, contexto, request=request)
            except Exception as exc:
                self._falha(f'Template {template_name} nao renderizou: {exc}')
            else:
                self._ok(f'Template {template_name} renderizou sem erro.')

    def _contexto_cronograma_template(self):
        data_inicio = date.today() - timedelta(days=date.today().weekday())
        data_fim = data_inicio + timedelta(days=6)
        dias_semana = ['Segunda-feira', 'Terca-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira']
        horarios = ['1', '2', '3', '4', '5', '6']
        return {
            'dias_semana': dias_semana,
            'horarios': horarios,
            'agendamentos_dict': {},
            'laboratorios': Laboratorio.objects.none(),
            'cronograma_por_laboratorio': [],
            'agendamentos_recentes': [],
            'data_inicio': data_inicio,
            'data_fim': data_fim,
            'semana_anterior': data_inicio - timedelta(days=7),
            'semana_proxima': data_inicio + timedelta(days=7),
            'turno_filtro': '',
            'laboratorio_filtro': '',
        }
