from .utilitarios import *

# =============================================================================
# GESTÃO DE LABORATÓRIOS E EMPRÉSTIMOS
# =============================================================================

from ..models import Laboratorio, ItemEquipamento, AgendamentoLab, Emprestimo
from django.db.models import Q
from django.urls import reverse
from datetime import datetime, timedelta


def _laboratorios_agendamento_queryset():
    return Laboratorio.objects.filter(ativo=True).order_by('nome')


def _contexto_agendamento(laboratorio, selected_laboratorio_id=None, form_data=None):
    from ..models import Professor, Turma

    return {
        'laboratorio': laboratorio,
        'laboratorios_agendamento': _laboratorios_agendamento_queryset(),
        'selected_laboratorio_id': int(selected_laboratorio_id or laboratorio.id),
        'form_data': form_data or {},
        'professores': Professor.objects.filter(ativo=True),
        'turmas': Turma.objects.filter(ativa=True),
    }


@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def listar_laboratorios(request):
    """Lista todos os laboratórios"""
    laboratorios = Laboratorio.objects.filter(ativo=True)

    # Contagem de disponíveis para itinerantes
    for lab in laboratorios:
        if lab.tipo == 'itinerante':
            lab.disponiveis = ItemEquipamento.objects.filter(
                laboratorio=lab,
                disponivel=True
            ).count()

    return render(request, 'laboratorios/listar.html', {'laboratorios': laboratorios})

@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def excluir_agendamento(request, agendamento_id):
    from django.shortcuts import get_object_or_404, redirect
    from django.contrib import messages
    from ..models import AgendamentoLab   # ajuste o nome do modelo se necessário

    agendamento = get_object_or_404(AgendamentoLab, id=agendamento_id)
    agendamento.delete()
    messages.success(request, "Agendamento removido com sucesso!")
    data_inicio = request.GET.get('data_inicio', '')
    url = reverse('cronograma_semanal')
    if data_inicio:
        url = f'{url}?data_inicio={data_inicio}'
    return redirect(url)

@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def agendamento_lab(request, lab_id):
    """Agendar laboratório fixo (Lab 01, 02, 03)"""
    laboratorio = get_object_or_404(Laboratorio, id=lab_id, ativo=True)

    if request.method == 'POST':
        laboratorio_id = request.POST.get('laboratorio') or lab_id
        data = request.POST.get('data')
        horario = request.POST.get('horario')
        turno = request.POST.get('turno')
        professor_id = request.POST.get('professor')
        disciplina = request.POST.get('disciplina')
        turma_id = request.POST.get('turma')
        observacao = request.POST.get('observacao', '')
        laboratorio = _laboratorios_agendamento_queryset().filter(id=laboratorio_id).first()

        if not laboratorio:
            laboratorio = get_object_or_404(Laboratorio, id=lab_id, ativo=True)
            messages.error(request, 'Selecione um laboratorio valido.')
            return render(request, 'laboratorios/agendar.html', _contexto_agendamento(
                laboratorio,
                selected_laboratorio_id=laboratorio.id,
                form_data=request.POST,
            ))

        # 1. Verificar se o mesmo professor já tem agendamento neste horário (em qualquer lab)
        conflito_professor = AgendamentoLab.objects.filter(
            professor_id=professor_id,
            data=data,
            horario=horario,
            turno=turno
        ).exists()

        if conflito_professor:
            messages.error(request, 'Este professor ja possui agendamento neste horario em outro laboratorio.')
            return render(request, 'laboratorios/agendar.html', _contexto_agendamento(
                laboratorio,
                selected_laboratorio_id=laboratorio.id,
                form_data=request.POST,
            ))

        # 2. Verificar se o laboratório já está reservado neste horário
        conflito_lab = AgendamentoLab.objects.filter(
            laboratorio=laboratorio,
            data=data,
            horario=horario,
            turno=turno
        ).exists()

        if conflito_lab:
            messages.error(request, f'{laboratorio.nome} ja esta reservado neste horario.')
            return render(request, 'laboratorios/agendar.html', _contexto_agendamento(
                laboratorio,
                selected_laboratorio_id=laboratorio.id,
                form_data=request.POST,
            ))

        # 3. Salvar agendamento
        AgendamentoLab.objects.create(
            laboratorio=laboratorio,
            data=data,
            horario=horario,
            turno=turno,
            professor_id=professor_id,
            disciplina=disciplina,
            turma_id=turma_id,
            observacao=observacao,
            registrado_por=request.user
        )
        messages.success(request, f'✅ {laboratorio.nome} agendado com sucesso!')
        return redirect('listar_laboratorios')

    return render(request, 'laboratorios/agendar.html', _contexto_agendamento(laboratorio))


@login_required
@login_required(login_url='/')
def cronograma_semanal(request):
    """Exibe o cronograma semanal dos laboratórios fixos, com filtro opcional por turno"""
    from datetime import datetime, timedelta

    # Pegar a semana (padrão: semana atual)
    data_inicio = request.GET.get('data_inicio')
    if data_inicio:
        data_inicio = datetime.strptime(data_inicio, '%Y-%m-%d').date()
    else:
        data_inicio = AgendamentoLab.objects.order_by('-data', '-id').values_list('data', flat=True).first()
        if not data_inicio:
            data_inicio = datetime.now().date()
        data_inicio = data_inicio - timedelta(days=data_inicio.weekday())

    data_fim = data_inicio + timedelta(days=6)

    # Capturar filtros opcionais
    turno_filtro = request.GET.get('turno', '')
    laboratorio_filtro = request.GET.get('laboratorio', '')
    agendamentos = AgendamentoLab.objects.filter(
        data__range=[data_inicio, data_fim],
    )
    laboratorios_filtrados = _laboratorios_agendamento_queryset()
    if laboratorio_filtro:
        laboratorios_filtrados = laboratorios_filtrados.filter(id=laboratorio_filtro)
        agendamentos = agendamentos.filter(laboratorio_id=laboratorio_filtro)

    # Aplicar filtro de turno se fornecido
    if turno_filtro in ['manha', 'tarde']:
        agendamentos = agendamentos.filter(turno=turno_filtro)

    agendamentos = agendamentos.select_related('laboratorio', 'professor', 'turma').order_by('-data', '-id')
    agendamentos_recentes = agendamentos

    # Mapa de dias em português
    mapa_dias = {
        'Monday': 'Segunda-feira',
        'Tuesday': 'Terça-feira',
        'Wednesday': 'Quarta-feira',
        'Thursday': 'Quinta-feira',
        'Friday': 'Sexta-feira'
    }

    # Criar dicionário para acesso rápido
    agendamentos_dict = {}
    for a in agendamentos:
        dia_en = a.data.strftime('%A')
        dia_pt = mapa_dias.get(dia_en, dia_en)
        key = (a.laboratorio.id, dia_pt, a.horario)
        agendamentos_dict.setdefault(key, []).append(a)

    dias_semana = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira']
    horarios = ['1', '2', '3', '4', '5', '6']

    cronograma_por_laboratorio = []
    for laboratorio in laboratorios_filtrados:
        linhas = []
        for horario in horarios:
            linhas.append({
                'horario': horario,
                'dias': [
                    {
                        'nome': dia,
                        'agendamentos': agendamentos_dict.get((laboratorio.id, dia, horario), []),
                    }
                    for dia in dias_semana
                ],
            })
        cronograma_por_laboratorio.append({
            'laboratorio': laboratorio,
            'linhas': linhas,
        })

    context = {
        'dias_semana': dias_semana,
        'horarios': horarios,
        'agendamentos_dict': agendamentos_dict,
        'laboratorios': _laboratorios_agendamento_queryset(),
        'cronograma_por_laboratorio': cronograma_por_laboratorio,
        'agendamentos_recentes': agendamentos_recentes,
        'data_inicio': data_inicio,
        'data_fim': data_fim,
        'semana_anterior': data_inicio - timedelta(days=7),
        'semana_proxima': data_inicio + timedelta(days=7),
        'turno_filtro': turno_filtro,
        'laboratorio_filtro': laboratorio_filtro,
    }
    return render(request, 'laboratorios/cronograma.html', context)

@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def cronograma_print(request):
    """Exibe o cronograma semanal para impressão (versão limpa)"""
    from datetime import datetime, timedelta

    # 📅 Datas
    data_inicio = request.GET.get('data_inicio')
    if data_inicio:
        data_inicio = datetime.strptime(data_inicio, '%Y-%m-%d').date()
    else:
        data_inicio = datetime.now().date()
        data_inicio = data_inicio - timedelta(days=data_inicio.weekday())

    data_fim = data_inicio + timedelta(days=6)

    # 🔥 CAPTURAR TURNO (FALTAVA ISSO)
    turno_filtro = request.GET.get('turno', '')

    # 📊 Buscar agendamentos
    agendamentos = AgendamentoLab.objects.filter(
        data__gte=data_inicio,
        data__lte=data_fim
    )

    # 🔥 APLICAR FILTRO (FALTAVA ISSO)
    if turno_filtro in ['manha', 'tarde']:
        agendamentos = agendamentos.filter(turno=turno_filtro)

    agendamentos = agendamentos.select_related('laboratorio', 'professor', 'turma').order_by('data', 'horario')

    # 📅 Dias
    mapa_dias = {
        'Monday': 'Segunda-feira',
        'Tuesday': 'Terça-feira',
        'Wednesday': 'Quarta-feira',
        'Thursday': 'Quinta-feira',
        'Friday': 'Sexta-feira'
    }

    # 📦 Dicionário
    agendamentos_dict = {}
    for a in agendamentos:
        dia_en = a.data.strftime('%A')
        dia_pt = mapa_dias.get(dia_en, dia_en)
        key = (a.laboratorio.id, dia_pt, a.horario)
        agendamentos_dict[key] = a

    dias_semana = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira']
    horarios = ['1', '2', '3', '4', '5', '6']

    # 📤 Contexto
    context = {
        'dias_semana': dias_semana,
        'horarios': horarios,
        'agendamentos_dict': agendamentos_dict,
        'laboratorios': Laboratorio.objects.filter(tipo='fixo', ativo=True),
        'data_inicio': data_inicio,
        'data_fim': data_fim,
        'semana_anterior': data_inicio - timedelta(days=7),
        'semana_proxima': data_inicio + timedelta(days=7),
        'turno_filtro': turno_filtro,
    }

    return render(request, 'laboratorios/cronograma_print.html', context)

    import openpyxl
from openpyxl.styles import Font, Alignment, PatternFill
from django.http import HttpResponse

@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def exportar_cronograma_excel(request):
    from datetime import datetime, timedelta
    from ..models import Laboratorio, AgendamentoLab
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    data_inicio = request.GET.get('data_inicio')
    if data_inicio:
        data_inicio = datetime.strptime(data_inicio, '%Y-%m-%d').date()
    else:
        data_inicio = datetime.now().date()
        data_inicio = data_inicio - timedelta(days=data_inicio.weekday())

    data_fim = data_inicio + timedelta(days=6)

    # Captura o filtro de turno (vem do template)
    turno_filtro = request.GET.get('turno', '')

    laboratorios = Laboratorio.objects.filter(tipo='fixo', ativo=True)
    dias_semana = ['Segunda-feira', 'Terça-feira', 'Quarta-feira', 'Quinta-feira', 'Sexta-feira']
    horarios = ['1', '2', '3', '4', '5', '6']

    mapa_dias = {
        'Monday': 'Segunda-feira',
        'Tuesday': 'Terça-feira',
        'Wednesday': 'Quarta-feira',
        'Thursday': 'Quinta-feira',
        'Friday': 'Sexta-feira'
    }

    # Buscar agendamentos da semana (com filtro de turno)
    agendamentos = AgendamentoLab.objects.filter(
        data__gte=data_inicio,
        data__lte=data_fim
    )
    if turno_filtro in ['manha', 'tarde']:
        agendamentos = agendamentos.filter(turno=turno_filtro)
    agendamentos = agendamentos.select_related('laboratorio', 'professor', 'turma')

    # Dicionário para acesso rápido
    agendamentos_dict = {}
    for a in agendamentos:
        dia_en = a.data.strftime('%A')
        dia_pt = mapa_dias.get(dia_en, dia_en)
        key = (a.laboratorio.id, dia_pt, a.horario)
        agendamentos_dict[key] = a

    wb = Workbook()
    wb.remove(wb.active)

    thin_border = Border(
        left=Side(style='thin'),
        right=Side(style='thin'),
        top=Side(style='thin'),
        bottom=Side(style='thin')
    )

    for lab in laboratorios:
        ws = wb.create_sheet(title=lab.nome)

        # Cabeçalho: dias da semana (uma coluna por dia)
        for idx, dia in enumerate(dias_semana, start=2):
            cell = ws.cell(row=1, column=idx, value=dia)
            cell.font = Font(bold=True, size=11)
            cell.alignment = Alignment(horizontal='center', vertical='center')
            cell.fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
            cell.border = thin_border

        # Agora, para cada horário, mesclar 3 linhas na coluna A e preencher as outras colunas
        row_offset = 2  # começa na linha 2 (após cabeçalho)
        for horario_idx, horario in enumerate(horarios):
            base_row = row_offset + horario_idx * 3

            # Mescla as 3 linhas na coluna A (horário)
            ws.merge_cells(start_row=base_row, start_column=1, end_row=base_row+2, end_column=1)
            cell_horario = ws.cell(row=base_row, column=1, value=f"{horario}ª Aula")
            cell_horario.font = Font(bold=True, size=11)
            cell_horario.alignment = Alignment(horizontal='center', vertical='center')
            cell_horario.border = thin_border

            # Para cada dia da semana
            for col_idx, dia in enumerate(dias_semana, start=2):
                key = (lab.id, dia, horario)
                a = agendamentos_dict.get(key)

                # Linha da disciplina
                ws.cell(row=base_row, column=col_idx, value=a.disciplina if a else "").border = thin_border
                ws.cell(row=base_row, column=col_idx).alignment = Alignment(horizontal='center', vertical='center')
                # Linha da turma
                ws.cell(row=base_row+1, column=col_idx, value=a.turma.nome if a else "").border = thin_border
                ws.cell(row=base_row+1, column=col_idx).alignment = Alignment(horizontal='center', vertical='center')
                # Linha do professor
                ws.cell(row=base_row + 2, column=col_idx,
                        value=a.professor.nome_abreviado if a else "").border = thin_border
                ws.cell(row=base_row+2, column=col_idx).alignment = Alignment(horizontal='center', vertical='center')

        # Ajustar larguras
        ws.column_dimensions['A'].width = 12
        for col in range(2, 2 + len(dias_semana)):
            col_letter = get_column_letter(col)
            ws.column_dimensions[col_letter].width = 28

        # Ajustar altura das linhas (cada bloco de 3 linhas = 60, cada linha 20)
        for row in range(row_offset, row_offset + len(horarios)*3):
            ws.row_dimensions[row].height = 20

    response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = f'attachment; filename="cronograma_{data_inicio.strftime("%Y%m%d")}.xlsx"'
    wb.save(response)
    return response


@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def emprestimo_equipamento(request, lab_id):
    """Registrar empréstimo de equipamentos (Lab 04 ou Lab 05)"""
    laboratorio = get_object_or_404(Laboratorio, id=lab_id, tipo='itinerante')

    # Equipamentos disponíveis
    equipamentos_disponiveis = ItemEquipamento.objects.filter(
        laboratorio=laboratorio,
        disponivel=True
    )

    if request.method == 'POST':
        tipo_emprestimo = request.POST.get('tipo_emprestimo')
        quantidade = int(request.POST.get('quantidade', 0))
        data_prevista = request.POST.get('data_prevista_devolucao')
        motivo = request.POST.get('motivo')
        aulas_utilizacao = request.POST.get('aulas_utilizacao', '')
        observacao = request.POST.get('observacao', '')

        # Pegar equipamentos selecionados (checkbox)
        itens_selecionados = request.POST.getlist('itens')

        # Validar
        if len(itens_selecionados) != quantidade:
            messages.error(request, 'Selecione a quantidade correta de equipamentos!')
        else:
            # Criar empréstimo
            emprestimo = Emprestimo.objects.create(
                tipo_emprestimo=tipo_emprestimo,
                laboratorio=laboratorio,
                quantidade=quantidade,
                data_prevista_devolucao=data_prevista,
                motivo=motivo,
                aulas_utilizacao=aulas_utilizacao,
                observacao=observacao,
                registrado_por=request.user
            )

            # Adicionar itens e marcar como indisponíveis
            for item_id in itens_selecionados:
                item = ItemEquipamento.objects.get(id=item_id)
                emprestimo.itens.add(item)
                item.disponivel = False
                item.save()

            # Se for professor
            if tipo_emprestimo == 'professor':
                emprestimo.professor_id = request.POST.get('professor_id')
            elif tipo_emprestimo == 'aluno':
                emprestimo.aluno_id = request.POST.get('aluno_id')
            elif tipo_emprestimo == 'turma':
                emprestimo.turma_id = request.POST.get('turma_id')

            emprestimo.save()
            messages.success(request, f'Empréstimo registrado! {quantidade} equipamento(s) emprestado(s).')
            return redirect('listar_laboratorios')

    # GET - mostrar formulário
    from ..models import Professor, Aluno, Turma
    professores = Professor.objects.filter(ativo=True)
    alunos = Aluno.objects.filter(ativo=True)
    turmas = Turma.objects.filter(ativa=True)

    return render(request, 'laboratorios/emprestimo.html', {
        'laboratorio': laboratorio,
        'equipamentos': equipamentos_disponiveis,
        'professores': professores,
        'alunos': alunos,
        'turmas': turmas,
    })


@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def devolver_equipamento(request, emprestimo_id):
    """Registrar devolução de equipamentos"""
    emprestimo = get_object_or_404(Emprestimo, id=emprestimo_id)

    if request.method == 'POST':
        from django.utils import timezone
        emprestimo.data_devolucao = timezone.now()
        emprestimo.status = 'devolvido'
        emprestimo.save()

        # Marcar equipamentos como disponíveis novamente
        for item in emprestimo.itens.all():
            item.disponivel = True
            item.save()

        messages.success(request, 'Equipamentos devolvidos com sucesso!')
        return redirect('listar_emprestimos')

    return render(request, 'laboratorios/devolver.html', {'emprestimo': emprestimo})


@login_required
@user_passes_test(pertence_ao_grupo_equipe_diretiva, login_url='/')
def listar_emprestimos(request):
    """Lista todos os empréstimos ativos"""
    emprestimos = Emprestimo.objects.filter(status='emprestado').order_by('data_prevista_devolucao')
    return render(request, 'laboratorios/lista_emprestimos.html', {'emprestimos': emprestimos})
