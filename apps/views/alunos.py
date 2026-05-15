from .utilitarios import *

@login_required
@user_passes_test(grupo_digitadores, login_url='/')
def formulario_digitador(request):
    """View exclusiva para digitadores (apenas o formulÃ¡rio de ocorrÃªncias)"""
    tipos_ocorrencia_permitidos = {'atraso', 'piercing', 'cabelo', 'uniforme', 'desvio_normas'}

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
            ocorrencia.tipo_ocorrencia = tipo_ocorrencia
            ocorrencia.faltou = False

            # ðŸ”§ CORREÃ‡ÃƒO 2: Pega o turno do formulÃ¡rio
            ocorrencia.turno = form.cleaned_data.get('turno', 'manha')

            if ocorrencia.horario_chegada == '':
                ocorrencia.horario_chegada = None
            if ocorrencia.horario_contato == '':
                ocorrencia.horario_contato = None

            ocorrencia.aluno = aluno
            ocorrencia.registrado_por = request.user
            ocorrencia.save()

            request.session['ultima_data_ocorrencia'] = ocorrencia.data.isoformat()
            request.session['ultima_turma_ocorrencia_id'] = turma.id
            request.session['ultima_turma_ocorrencia_nome'] = turma.nome

            messages.success(request, f'OcorrÃªncia registrada para {aluno.nome} (Turma {turma.nome}, NÂº {numero})')
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
