from .utilitarios import *
import logging

from apps.services.whatsapp_service import enviar_template_aviso_ocorrencia_aluno


logger = logging.getLogger(__name__)

TIPOS_REINCIDENCIA_WHATSAPP = {'piercing', 'desvio_normas', 'cabelo', 'uniforme'}
TIPOS_OCORRENCIA_WHATSAPP = {
    'piercing',
    'desvio_normas',
    'cabelo',
    'uniforme',
    'fora_sala',
    'matando_aula',
}
TIPOS_OCORRENCIA_PERMITIDOS = TIPOS_OCORRENCIA_WHATSAPP | {'atraso'}


def _telefone_whatsapp_aluno(aluno):
    for campo in ('telefone_responsavel', 'telefone'):
        telefone = getattr(aluno, campo, None)
        if telefone is None:
            continue
        telefone = str(telefone).strip()
        if telefone:
            return telefone
    return ''


def _data_ocorrencia_formatada(data_ocorrencia):
    try:
        return datetime.strptime(str(data_ocorrencia), '%Y-%m-%d').strftime('%d/%m/%Y')
    except ValueError:
        return str(data_ocorrencia)


def _tipo_ocorrencia_base(tipo_ocorrencia):
    tipo_ocorrencia = tipo_ocorrencia or ''
    if '_r' in tipo_ocorrencia:
        base, reincidencia = tipo_ocorrencia.rsplit('_r', 1)
        if reincidencia.isdigit():
            return base
    return tipo_ocorrencia


def _rotulo_tipo_ocorrencia(tipo_ocorrencia):
    return {
        'atraso': 'atraso',
        'piercing': 'piercing',
        'cabelo': 'cabelo',
        'uniforme': 'uniforme',
        'desvio_normas': 'desvio de normas',
        'fora_sala': 'fora de sala sem autorizacao',
        'matando_aula': 'matando aula',
        'falta': 'falta',
    }.get(_tipo_ocorrencia_base(tipo_ocorrencia), tipo_ocorrencia)


def _ocorrencias_mesmo_tipo_no_dia(aluno, data_ocorrencia, tipo_ocorrencia):
    base = _tipo_ocorrencia_base(tipo_ocorrencia)
    return RegistroOcorrenciaAluno.objects.filter(
        aluno=aluno,
        data=data_ocorrencia,
    ).filter(Q(tipo_ocorrencia=base) | Q(tipo_ocorrencia__startswith=f'{base}_r'))


def _tipo_reincidencia_para_salvar(tipo_ocorrencia, quantidade_existente):
    if quantidade_existente == 0:
        return tipo_ocorrencia
    return f'{tipo_ocorrencia}_r{quantidade_existente + 1}'


def _notificar_ocorrencia_whatsapp(request, ocorrencia):
    tipo_base = _tipo_ocorrencia_base(ocorrencia.tipo_ocorrencia)
    if tipo_base == 'falta':
        messages.success(request, 'Ocorrencia registrada. WhatsApp nao enviado para tipo falta.')
        return
    if tipo_base not in TIPOS_OCORRENCIA_WHATSAPP:
        return

    telefone = _telefone_whatsapp_aluno(ocorrencia.aluno)
    if not telefone:
        logger.warning(
            'Ocorrencia registrada sem envio de WhatsApp: aluno sem telefone cadastrado.',
            extra={
                'aluno': ocorrencia.aluno.nome,
                'aluno_id': ocorrencia.aluno_id,
                'data_ocorrencia': str(ocorrencia.data),
                'tipo_ocorrencia': ocorrencia.tipo_ocorrencia,
            },
        )
        messages.warning(request, 'Ocorrencia registrada, mas confira o telefone do responsavel.')
        return

    data_ocorrencia = _data_ocorrencia_formatada(ocorrencia.data)
    resultado_whatsapp = enviar_template_aviso_ocorrencia_aluno(
        telefone,
        ocorrencia.aluno.nome,
        _rotulo_tipo_ocorrencia(ocorrencia.tipo_ocorrencia),
        data_ocorrencia,
    )

    if resultado_whatsapp.get('status'):
        messages.success(request, 'Ocorrencia registrada e aviso enviado pelo WhatsApp.')
    else:
        logger.warning(
            'Ocorrencia registrada, mas envio de WhatsApp falhou.',
            extra={
                'aluno': ocorrencia.aluno.nome,
                'aluno_id': ocorrencia.aluno_id,
                'numero_usado': resultado_whatsapp.get('numero') or telefone,
                'data_ocorrencia': str(ocorrencia.data),
                'tipo_ocorrencia': ocorrencia.tipo_ocorrencia,
                'erro_whatsapp': resultado_whatsapp.get('erro'),
                'resposta_whatsapp': resultado_whatsapp.get('resposta'),
                'status_code_whatsapp': resultado_whatsapp.get('status_code'),
            },
        )
        messages.warning(request, 'Ocorrencia registrada, mas o aviso nao foi enviado. Confira o numero do responsavel.')


@login_required
@user_passes_test(grupo_digitadores, login_url='/')
def formulario_digitador(request):
    """View exclusiva para digitadores (apenas o formulÃ¡rio de ocorrÃªncias)"""
    tipos_ocorrencia_permitidos = TIPOS_OCORRENCIA_PERMITIDOS

    # Recupera Ãºltima data da sessÃ£o
    ultima_data_str = request.session.get('ultima_data_ocorrencia')
    if ultima_data_str:
        try:
            ultima_data = datetime.strptime(ultima_data_str, '%Y-%m-%d').date()
        except ValueError:
            ultima_data = timezone.now().date()
    else:
        ultima_data = timezone.now().date()

    # Recupera Ãºltima turma da sessÃ£o
    ultima_turma_id = request.session.get('ultima_turma_ocorrencia_id')
    ultima_turma = None
    if ultima_turma_id:
        try:
            ultima_turma = Turma.objects.get(id=ultima_turma_id)
        except Turma.DoesNotExist:
            ultima_turma = None

    if request.method == 'POST':
        form = RegistroOcorrenciaForm(request.POST)
        if form.is_valid():
            turma = form.cleaned_data['turma']
            numero = form.cleaned_data['numero_aluno']

            aluno = Aluno.objects.filter(turma=turma, numero=numero).first()
            if not aluno:
                messages.error(request, f'Aluno nÃºmero {numero} nÃ£o encontrado na turma {turma.nome}!')
                return render(request, 'ocorrencias/formulario_digitador.html', {
                    'form': form,
                    'ultimas_ocorrencias': RegistroOcorrenciaAluno.objects.select_related('aluno', 'aluno__turma').order_by('-data', '-horario_chegada')[:10]
                })

            ocorrencia = form.save(commit=False)

            # ðŸ”§ CORREÃ‡ÃƒO 1: Pega o tipo de ocorrÃªncia do formulÃ¡rio
            tipo_ocorrencia = request.POST.get('tipo_ocorrencia', 'atraso')
            if tipo_ocorrencia not in tipos_ocorrencia_permitidos:
                tipo_ocorrencia = 'atraso'
            ocorrencia.faltou = False

            # ðŸ”§ CORREÃ‡ÃƒO 2: Pega o turno do formulÃ¡rio
            ocorrencia.turno = form.cleaned_data.get('turno', 'manha')

            if ocorrencia.horario_chegada == '':
                ocorrencia.horario_chegada = None
            if ocorrencia.horario_contato == '':
                ocorrencia.horario_contato = None

            ocorrencia.aluno = aluno
            ocorrencia.registrado_por = request.user
            ocorrencias_mesmo_tipo = _ocorrencias_mesmo_tipo_no_dia(aluno, ocorrencia.data, tipo_ocorrencia)
            quantidade_mesmo_tipo = ocorrencias_mesmo_tipo.count()
            ocorrencias_do_dia = RegistroOcorrenciaAluno.objects.filter(
                aluno=aluno,
                data=ocorrencia.data,
            )
            ocorrencia_igual_no_dia = quantidade_mesmo_tipo > 0
            ocorrencia_diferente_no_dia = ocorrencias_do_dia.exclude(
                Q(tipo_ocorrencia=tipo_ocorrencia) | Q(tipo_ocorrencia__startswith=f'{tipo_ocorrencia}_r')
            ).exists()
            tipo_permite_reincidencia = tipo_ocorrencia in TIPOS_REINCIDENCIA_WHATSAPP
            if ocorrencia_igual_no_dia and not tipo_permite_reincidencia:
                messages.warning(request, 'Ja existe ocorrencia igual para este aluno nesta data.')
                return redirect('formulario_digitador')
            ocorrencia.tipo_ocorrencia = _tipo_reincidencia_para_salvar(tipo_ocorrencia, quantidade_mesmo_tipo)

            ocorrencia.save()

            request.session['ultima_data_ocorrencia'] = ocorrencia.data.isoformat()
            request.session['ultima_turma_ocorrencia_id'] = turma.id
            request.session['ultima_turma_ocorrencia_nome'] = turma.nome

            messages.success(request, f'OcorrÃªncia registrada para {aluno.nome} (Turma {turma.nome}, NÂº {numero})')
            if ocorrencia_diferente_no_dia:
                messages.warning(request, 'Ja existe outra ocorrencia para este aluno nesta data. WhatsApp nao enviado automaticamente.')
            elif ocorrencia_igual_no_dia:
                if request.POST.get('confirmar_reenvio_whatsapp') == '1':
                    _notificar_ocorrencia_whatsapp(request, ocorrencia)
                else:
                    messages.warning(request, 'Esta e a segunda ocorrencia deste tipo hoje. Deseja enviar novo aviso pelo WhatsApp?')
            else:
                _notificar_ocorrencia_whatsapp(request, ocorrencia)
            return redirect('formulario_digitador')
        else:
            messages.error(request, 'Erro no formulÃ¡rio. Verifique os dados.')
    else:
        initial_data = {
            'data': ultima_data.isoformat(),
            'faltou': False,
        }
        form = RegistroOcorrenciaForm(initial=initial_data)
        if ultima_turma:
            form.fields['turma'].initial = ultima_turma

    ultimas_ocorrencias = RegistroOcorrenciaAluno.objects.select_related('aluno', 'aluno__turma').order_by('-data', '-horario_chegada')[:10]

    return render(request, 'ocorrencias/formulario_digitador.html', {
        'form': form,
        'ultimas_ocorrencias': ultimas_ocorrencias
    })


@ocorrencias_required
def registrar_ocorrencia_aluno(request):
    tipos_ocorrencia_permitidos = TIPOS_OCORRENCIA_PERMITIDOS

    ultima_data_str = request.session.get('ultima_data_ocorrencia')
    if ultima_data_str:
        try:
            ultima_data = datetime.strptime(ultima_data_str, '%Y-%m-%d').date()
        except ValueError:
            ultima_data = timezone.now().date()
    else:
        ultima_data = timezone.now().date()

    ultima_turma_id = request.session.get('ultima_turma_ocorrencia_id')
    ultima_turma = None
    if ultima_turma_id:
        try:
            ultima_turma = Turma.objects.get(id=ultima_turma_id)
        except Turma.DoesNotExist:
            ultima_turma = None

    if request.method == 'POST':
        pedagoga = request.POST.get('pedagoga', '').strip()
        if pedagoga == 'Outra':
            pedagoga = request.POST.get('pedagoga_outra', '').strip()

        if not pedagoga:
            messages.error(request, 'Selecione ou informe a pedagoga responsavel.')
            return redirect('registrar_ocorrencia_aluno')

        form = RegistroOcorrenciaForm(request.POST)
        if form.is_valid():
            turma = form.cleaned_data['turma']
            numero = form.cleaned_data['numero_aluno']

            aluno = Aluno.objects.filter(turma=turma, numero=numero).first()
            if not aluno:
                messages.error(request, f'Aluno numero {numero} nao encontrado na turma {turma.nome}!')
                contexto = {
                    'form': form,
                    'ultimas_ocorrencias': RegistroOcorrenciaAluno.objects.select_related('aluno', 'aluno__turma').order_by('-data', '-horario_chegada')[:10],
                }
                return render(request, 'ocorrencias/registrar_ocorrencia.html', contexto)

            ocorrencia = form.save(commit=False)
            tipo_ocorrencia = request.POST.get('tipo_ocorrencia', 'atraso')
            if tipo_ocorrencia not in tipos_ocorrencia_permitidos:
                tipo_ocorrencia = 'atraso'
            ocorrencia.observacoes_adicionais = request.POST.get('observacoes_adicionais', '')
            ocorrencia.atendido_por = pedagoga
            ocorrencia.turno = aluno.turma.turno
            ocorrencia.faltou = False

            if ocorrencia.horario_chegada == '':
                ocorrencia.horario_chegada = None
            if ocorrencia.horario_contato == '':
                ocorrencia.horario_contato = None

            ocorrencia.aluno = aluno
            ocorrencia.registrado_por = request.user
            ocorrencias_mesmo_tipo = _ocorrencias_mesmo_tipo_no_dia(aluno, ocorrencia.data, tipo_ocorrencia)
            quantidade_mesmo_tipo = ocorrencias_mesmo_tipo.count()
            ocorrencias_do_dia = RegistroOcorrenciaAluno.objects.filter(
                aluno=aluno,
                data=ocorrencia.data,
            )
            ocorrencia_igual_no_dia = quantidade_mesmo_tipo > 0
            ocorrencia_diferente_no_dia = ocorrencias_do_dia.exclude(
                Q(tipo_ocorrencia=tipo_ocorrencia) | Q(tipo_ocorrencia__startswith=f'{tipo_ocorrencia}_r')
            ).exists()
            tipo_permite_reincidencia = tipo_ocorrencia in TIPOS_REINCIDENCIA_WHATSAPP
            if ocorrencia_igual_no_dia and not tipo_permite_reincidencia:
                messages.warning(request, 'Ja existe ocorrencia igual para este aluno nesta data.')
                return redirect('registrar_ocorrencia_aluno')
            ocorrencia.tipo_ocorrencia = _tipo_reincidencia_para_salvar(tipo_ocorrencia, quantidade_mesmo_tipo)

            try:
                ocorrencia.save()
            except IntegrityError:
                messages.warning(request, 'Ja existe ocorrencia igual para este aluno nesta data.')
                return redirect('registrar_ocorrencia_aluno')

            request.session['ultima_data_ocorrencia'] = ocorrencia.data.isoformat()
            request.session['ultima_turma_ocorrencia_id'] = turma.id
            request.session['ultima_turma_ocorrencia_nome'] = turma.nome
            request.session['ultimo_tipo_ocorrencia'] = tipo_ocorrencia
            request.session['ultimo_turno'] = request.POST.get('turno', 'manha')

            messages.success(request, f'Ocorrencia registrada para {aluno.nome} (Turma {turma.nome}, No {numero})')
            if ocorrencia_diferente_no_dia:
                messages.warning(request, 'Ja existe outra ocorrencia para este aluno nesta data. WhatsApp nao enviado automaticamente.')
            elif ocorrencia_igual_no_dia:
                if request.POST.get('confirmar_reenvio_whatsapp') == '1':
                    _notificar_ocorrencia_whatsapp(request, ocorrencia)
                else:
                    messages.warning(request, 'Esta e a segunda ocorrencia deste tipo hoje. Deseja enviar novo aviso pelo WhatsApp?')
            else:
                _notificar_ocorrencia_whatsapp(request, ocorrencia)
            return redirect('registrar_ocorrencia_aluno')

        messages.error(request, 'Erro no formulario. Verifique os dados.')
        return redirect('registrar_ocorrencia_aluno')

    ultimo_tipo = request.session.get('ultimo_tipo_ocorrencia', 'atraso')
    if ultimo_tipo not in tipos_ocorrencia_permitidos:
        ultimo_tipo = 'atraso'
    ultimo_turno = request.session.get('ultimo_turno', 'manha')

    initial_data = {
        'data': ultima_data.isoformat(),
        'faltou': False,
        'tipo_ocorrencia': ultimo_tipo,
        'turno': ultimo_turno,
    }
    form = RegistroOcorrenciaForm(initial=initial_data)
    if ultima_turma:
        form.fields['turma'].initial = ultima_turma

    ultimas_ocorrencias = RegistroOcorrenciaAluno.objects.select_related('aluno', 'aluno__turma').order_by('-data', '-horario_chegada')[:10]

    contexto = {
        'form': form,
        'ultimas_ocorrencias': ultimas_ocorrencias,
    }
    return render(request, 'ocorrencias/registrar_ocorrencia.html', contexto)


# =============================================================================
# IMPORTAÃ‡ÃƒO DE ALUNOS VIA EXCEL
# =============================================================================
from openpyxl import load_workbook
from ..models import Turma, Aluno

@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def importar_alunos_excel(request):
    if request.method == 'POST' and request.FILES.get('arquivo'):
        arquivo = request.FILES['arquivo']
        wb = load_workbook(arquivo)
        ws = wb.active

        contador = 0
        erros = 0

        for row in ws.iter_rows(min_row=2, values_only=True):  # Pula cabeÃ§alho
            try:
                turma_nome = str(row[0]).strip() if row[0] else ''  # Coluna A: Turma (ex: 3A)
                numero = int(row[1]) if row[1] else 0               # Coluna B: NÃºmero
                nome = str(row[2]).strip() if row[2] else ''        # Coluna C: Nome do aluno

                if turma_nome and numero and nome:
                    # Extrai a sÃ©rie do nome da turma (ex: 3A -> 3Âº Ano)
                    serie = f"{turma_nome[0]}Âº Ano"

                    # Busca ou cria a turma
                    turma, created = Turma.objects.get_or_create(
                        nome=turma_nome,
                        defaults={
                            'serie': serie,
                            'ano': 2026,
                            'ativa': True
                        }
                    )

                    # Cria o aluno
                    Aluno.objects.create(
                        nome=nome,
                        numero=numero,
                        turma=turma
                    )
                    contador += 1
                else:
                    erros += 1
            except Exception as e:
                erros += 1
                print(f"Erro na linha: {e}")

        messages.success(request, f'{contador} alunos importados com sucesso!')
        if erros > 0:
            messages.warning(request, f'{erros} linhas com erro foram ignoradas.')

        return redirect('controle_faltas_alunos')

    return render(request, 'importar_alunos.html')


@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def cadastrar_aluno(request):
    if request.method == 'POST':
        form = AlunoForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, 'âœ… Aluno cadastrado com sucesso!')
            return redirect('painel_equipe')
    else:
        form = AlunoForm()

    return render(request, 'cadastrar_aluno.html', {'form': form})


@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def listar_alunos(request):
    alunos = Aluno.objects.all().order_by('turma', 'numero')
    return render(request, 'listar_alunos.html', {'alunos': alunos})


@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def editar_aluno(request, aluno_id):
    aluno = get_object_or_404(Aluno, id=aluno_id)
    if request.method == 'POST':
        form = AlunoEditForm(request.POST, instance=aluno)
        if form.is_valid():
            form.save()
            messages.success(request, f'âœ… Aluno {aluno.nome} atualizado!')
            return redirect('listar_alunos')
    else:
        form = AlunoEditForm(instance=aluno)

    return render(request, 'editar_aluno.html', {'form': form, 'aluno': aluno})


# =============================================================================
# CADASTRO E EDIÃ‡ÃƒO DE ALUNOS (jÃ¡ existente)
# =============================================================================

@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def editar_aluno(request, aluno_id):
    aluno = get_object_or_404(Aluno, id=aluno_id)
    if request.method == 'POST':
        form = AlunoEditForm(request.POST, instance=aluno)
        if form.is_valid():
            form.save()
            messages.success(request, f'âœ… Aluno {aluno.nome} atualizado!')
            return redirect('listar_alunos')
    else:
        form = AlunoEditForm(instance=aluno)

    return render(request, 'editar_aluno.html', {'form': form, 'aluno': aluno})
