#!/usr/bin/env python3
"""
Dashboard de Acompanhamento Pós-Instalação
Porta 5009 | Fonte de contatos: su_ticket (IXC)
"""
from flask import Flask, jsonify, render_template, request, Response
import csv, io
from dotenv import load_dotenv
import mysql.connector
import os, time, threading
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent / '.env')

MYSQL_CONFIG = {
    "host":     os.getenv("MYSQL_HOST"),
    "database": os.getenv("MYSQL_DATABASE"),
    "user":     os.getenv("MYSQL_USER"),
    "password": os.getenv("MYSQL_PASSWORD"),
    "port":     int(os.getenv("MYSQL_PORT", 3306)),
    "connection_timeout": 30,
    "charset":  "utf8mb4",
}

app = Flask(__name__)

_cache, _lock = {}, threading.Lock()
TTL = 300

def cache_get(k):
    with _lock:
        e = _cache.get(k)
        if e and time.time() - e['ts'] < TTL:
            return e['data']
    return None

def cache_set(k, d):
    with _lock:
        _cache[k] = {'data': d, 'ts': time.time()}

def get_db():
    return mysql.connector.connect(**MYSQL_CONFIG)

def p_janela():
    try:
        v = int(request.args.get('janela', 30))
        return v if v in (30, 60, 90) else 30
    except Exception:
        return 30

# ── Filtros de ativação — alinhados com o script IXC de referência ─────────────
# status <> 'P': exclui contratos pendentes
# razao NOT LIKE '%TESTE%': exclui clientes de teste
# motivo_inclusao IN ('I','M'): instalações e mudanças de endereço
# Requer: JOIN cliente c ON c.id = cc.id_cliente
BASE_WHERE = """
    cc.motivo_inclusao IN ('I','M')
    AND cc.data_ativacao >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
    AND cc.data_ativacao <= CURDATE()
    AND cc.status <> 'P'
    AND cc.data_ativacao IS NOT NULL
    AND c.razao NOT LIKE '%%TESTE%%'
"""

# ── Exclusões de tickets internos ──────────────────────────────────────────────
_EXCL = """
    AND a2.assunto NOT LIKE '%AGENDAMENTO%SERVI%'
    AND a2.assunto NOT LIKE '%REAGENDAMENTO%SERVI%'
    AND a2.assunto NOT LIKE '%FALTA DE INTERA%'
    AND a2.assunto NOT LIKE '%ATRASO%AGENDA%'
    AND a2.assunto NOT LIKE '%ATRASO%ROTA%'
"""

TICKET_STATUS = {
    'T':'Triagem','C':'Cancelado','F':'Finalizado',
    'EX':'Em Execução','OSAB':'OS Aberta','OSAG':'OS Agendada','OSEX':'OS Execução',
}

def build_ticket_join(filtro_assunto=''):
    """Retorna (sql_fragment, params_list) para o LEFT JOIN de su_ticket."""
    assunto_cond = "AND a2.assunto = %s" if filtro_assunto else ""
    sql = f"""
        LEFT JOIN su_ticket t
            ON  t.id_cliente   = cc.id_cliente
            AND t.data_criacao >= cc.data_ativacao
            AND t.data_criacao <  DATE_ADD(cc.data_ativacao, INTERVAL 30 DAY)
            AND t.id_assunto IS NOT NULL
            AND EXISTS (
                SELECT 1 FROM su_oss_assunto a2
                WHERE a2.id = t.id_assunto
                  {_EXCL}
                  {assunto_cond}
            )
    """
    return sql, ([filtro_assunto] if filtro_assunto else [])


@app.route('/')
def index():
    return render_template('index.html')


BASE_WHERE_PREV = """
    cc.motivo_inclusao IN ('I','M')
    AND cc.data_ativacao >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
    AND cc.data_ativacao <  DATE_SUB(CURDATE(), INTERVAL %s DAY)
    AND cc.status <> 'P'
    AND cc.data_ativacao IS NOT NULL
    AND c.razao NOT LIKE '%%TESTE%%'
"""

@app.route('/api/kpis')
def api_kpis():
    janela = p_janela()
    ck = f'kpis_{janela}'
    if (c := cache_get(ck)): return jsonify(c)

    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        # ── Período atual ──────────────────────────────────────────────────
        cur.execute(f"""
            SELECT COUNT(DISTINCT cc.id) AS total,
                   SUM(cc.motivo_inclusao='I') AS inst,
                   SUM(cc.motivo_inclusao='M') AS mud
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            WHERE {BASE_WHERE}
        """, (janela,))
        av = cur.fetchone()

        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT COUNT(DISTINCT CASE WHEN t.id IS NOT NULL THEN cc.id_cliente END) AS com,
                   COUNT(t.id) AS tot
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            {tj}
            WHERE {BASE_WHERE}
        """, jp + [janela])
        ct = cur.fetchone()

        cur.execute(f"""
            SELECT AVG(d) AS media FROM (
                SELECT DATEDIFF(MIN(t.data_criacao), cc.data_ativacao) AS d
                FROM cliente_contrato cc
                JOIN cliente c ON c.id = cc.id_cliente
                {tj}
                WHERE {BASE_WHERE}
                  AND t.id IS NOT NULL
                GROUP BY cc.id_cliente, cc.data_ativacao
            ) sub
        """, jp + [janela])
        med = cur.fetchone()['media']

        # ── Período anterior (janela equivalente imediatamente antes) ──────
        cur.execute(f"""
            SELECT COUNT(DISTINCT cc.id) AS total,
                   COUNT(DISTINCT CASE WHEN t.id IS NOT NULL THEN cc.id_cliente END) AS com,
                   COUNT(t.id) AS tot
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            {tj}
            WHERE {BASE_WHERE_PREV}
        """, jp + [janela * 2, janela])
        prev = cur.fetchone()

        total   = int(av['total'] or 0)
        com     = int(ct['com'] or 0)
        pct     = round(com / total * 100, 1) if total else 0
        p_total = int(prev['total'] or 0)
        p_com   = int(prev['com'] or 0)
        p_pct   = round(p_com / p_total * 100, 1) if p_total else 0

        def delta(curr, prev):
            if prev == 0: return None
            return round(((curr - prev) / prev) * 100, 1)

        res = {
            'total': total, 'instalacoes': int(av['inst'] or 0),
            'mudancas': int(av['mud'] or 0), 'com_contato': com,
            'sem_contato': total - com,
            'pct': pct,
            'total_contatos': int(ct['tot'] or 0),
            'media_dias': round(float(med), 1) if med else 0,
            'janela': janela,
            # Deltas vs período anterior
            'delta_total': delta(total, p_total),
            'delta_pct':   round(pct - p_pct, 1) if p_total else None,
            'delta_tickets': delta(int(ct['tot'] or 0), int(prev['tot'] or 0)),
            'prev_total': p_total, 'prev_pct': p_pct,
        }
        cache_set(ck, res)
        return jsonify(res)
    finally:
        cur.close(); conn.close()


@app.route('/api/motivos')
def api_motivos():
    janela = p_janela()
    ck = f'motivos_{janela}'
    if (c := cache_get(ck)): return jsonify(c)

    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT a2.assunto, COUNT(t.id) AS qtd
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            {tj}
            JOIN su_oss_assunto a2 ON a2.id = t.id_assunto
            WHERE {BASE_WHERE}
              AND t.id IS NOT NULL
            GROUP BY a2.assunto
            ORDER BY qtd DESC LIMIT 12
        """, jp + [janela])
        rows = cur.fetchall()
        res = {'labels': [r['assunto'] for r in rows], 'valores': [r['qtd'] for r in rows]}
        cache_set(ck, res)
        return jsonify(res)
    finally:
        cur.close(); conn.close()


@app.route('/api/distribuicao')
def api_distribuicao():
    janela = p_janela()
    ck = f'distrib_{janela}'
    if (c := cache_get(ck)): return jsonify(c)

    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT
                CASE
                    WHEN d=0             THEN 'Dia 0'
                    WHEN d BETWEEN 1 AND 3  THEN '1–3 dias'
                    WHEN d BETWEEN 4 AND 7  THEN '4–7 dias'
                    WHEN d BETWEEN 8 AND 14 THEN '8–14 dias'
                    WHEN d BETWEEN 15 AND 21 THEN '15–21 dias'
                    ELSE '22–30 dias'
                END AS faixa, COUNT(*) AS qtd
            FROM (
                SELECT DATEDIFF(MIN(t.data_criacao), cc.data_ativacao) AS d
                FROM cliente_contrato cc
                JOIN cliente c ON c.id = cc.id_cliente
                {tj}
                WHERE {BASE_WHERE}
                  AND t.id IS NOT NULL
                GROUP BY cc.id_cliente, cc.data_ativacao
            ) sub
            GROUP BY faixa
            ORDER BY FIELD(faixa,'Dia 0','1–3 dias','4–7 dias','8–14 dias','15–21 dias','22–30 dias')
        """, jp + [janela])
        rows = cur.fetchall()
        res = {'labels': [r['faixa'] for r in rows], 'valores': [r['qtd'] for r in rows]}
        cache_set(ck, res)
        return jsonify(res)
    finally:
        cur.close(); conn.close()


@app.route('/api/assuntos-lista')
def api_assuntos_lista():
    janela = p_janela()
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT DISTINCT a2.assunto
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            {tj}
            JOIN su_oss_assunto a2 ON a2.id = t.id_assunto
            WHERE {BASE_WHERE}
              AND t.id IS NOT NULL
            ORDER BY a2.assunto
        """, jp + [janela])
        return jsonify([r['assunto'] for r in cur.fetchall()])
    finally:
        cur.close(); conn.close()


@app.route('/api/clientes')
def api_clientes():
    janela         = p_janela()
    page           = max(1, int(request.args.get('page', 1)))
    per_page       = 25
    offset         = (page - 1) * per_page
    so_contato     = request.args.get('so_contato', '0') == '1'
    busca          = request.args.get('busca', '').strip()
    filtro_assunto = request.args.get('assunto', '').strip()
    min_tickets    = max(0, int(request.args.get('min_tickets', 0) or 0))

    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join(filtro_assunto)
        extra_where = ""
        extra_p = []
        if busca:
            extra_where = " AND c.razao LIKE %s"
            extra_p = [f'%{busca}%']

        if min_tickets > 0:
            having = f"HAVING COUNT(t.id) >= {min_tickets}"
        elif so_contato:
            having = "HAVING COUNT(t.id) > 0"
        else:
            having = ""
        base_p = jp + [janela] + extra_p

        sql = f"""
            SELECT cc.id AS contrato_id, cc.id_cliente,
                   c.razao AS nome, c.telefone_celular, c.fone,
                   cc.data_ativacao, cc.motivo_inclusao,
                   COUNT(t.id)                               AS total_contatos,
                   MIN(t.data_criacao)                       AS primeiro_contato,
                   DATEDIFF(MIN(t.data_criacao), cc.data_ativacao) AS dias_primeiro
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            {tj}
            WHERE {BASE_WHERE}
            {extra_where}
            GROUP BY cc.id, cc.id_cliente, c.razao, c.telefone_celular,
                     c.fone, cc.data_ativacao, cc.motivo_inclusao
            {having}
            ORDER BY total_contatos DESC, cc.data_ativacao DESC
            LIMIT %s OFFSET %s
        """
        cur.execute(sql, base_p + [per_page, offset])
        rows = cur.fetchall()

        sql_cnt = f"""
            SELECT COUNT(*) AS total FROM (
                SELECT cc.id
                FROM cliente_contrato cc
                JOIN cliente c ON c.id = cc.id_cliente
                {tj}
                WHERE {BASE_WHERE}
                {extra_where}
                GROUP BY cc.id
                {having}
            ) sub
        """
        cur.execute(sql_cnt, base_p)
        total = cur.fetchone()['total']

        for r in rows:
            r['data_ativacao']    = str(r['data_ativacao'])    if r['data_ativacao']    else None
            r['primeiro_contato'] = str(r['primeiro_contato']) if r['primeiro_contato'] else None
            r['total_contatos']   = int(r['total_contatos'])
            r['dias_primeiro']    = int(r['dias_primeiro'])    if r['dias_primeiro'] is not None else None

        return jsonify({'rows': rows, 'total': total, 'page': page,
                        'pages': max(1, (total + per_page - 1) // per_page)})
    finally:
        cur.close(); conn.close()


@app.route('/api/contatos/<int:cliente_id>')
def api_contatos(cliente_id):
    janela = p_janela()
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT t.id AS ticket_id, t.data_criacao, t.status, t.protocolo,
                   a2.assunto,
                   DATEDIFF(t.data_criacao, cc.data_ativacao) AS dias_apos
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            {tj}
            JOIN su_oss_assunto a2 ON a2.id = t.id_assunto
            WHERE cc.id_cliente = %s
              AND {BASE_WHERE}
              AND t.id IS NOT NULL
            ORDER BY t.data_criacao
        """, jp + [cliente_id, janela])
        rows = cur.fetchall()
        for r in rows:
            r['data_criacao'] = str(r['data_criacao']) if r['data_criacao'] else None
            r['status_label'] = TICKET_STATUS.get(r['status'], r['status'])
            r['dias_apos']    = int(r['dias_apos']) if r['dias_apos'] is not None else 0
        return jsonify(rows)
    finally:
        cur.close(); conn.close()


@app.route('/api/tendencia')
def api_tendencia():
    janela = p_janela()
    # Janela → semanas exibidas: 30d=13w, 60d=20w, 90d=26w
    semanas = {30: 13, 60: 20, 90: 26}.get(janela, 13)
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT
                YEARWEEK(cc.data_ativacao, 1)        AS semana,
                DATE(MIN(cc.data_ativacao))           AS inicio,
                COUNT(DISTINCT cc.id)                 AS ativacoes,
                COUNT(DISTINCT CASE WHEN t.id IS NOT NULL THEN cc.id END) AS com_contato
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            {tj}
            WHERE cc.motivo_inclusao IN ('I','M')
              AND cc.status <> 'P'
              AND cc.data_ativacao IS NOT NULL
              AND c.razao NOT LIKE '%%TESTE%%'
              AND cc.data_ativacao >= DATE_SUB(CURDATE(), INTERVAL %s WEEK)
              AND cc.data_ativacao <= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
            GROUP BY semana ORDER BY semana
        """, jp + [semanas])
        rows = cur.fetchall()
        result = []
        for r in rows:
            a = int(r['ativacoes']); ct = int(r['com_contato'])
            result.append({
                'semana': str(r['semana']), 'inicio': str(r['inicio']),
                'ativacoes': a, 'com_contato': ct,
                'taxa': round(ct/a*100, 1) if a else 0
            })
        return jsonify(result)
    finally:
        cur.close(); conn.close()


@app.route('/api/churn')
def api_churn():
    # Fonte: data_cancelamento de cliente_contrato (consistente com Power BI)
    # NULLIF(..., '0001-01-01') trata o valor nulo padrão do MariaDB
    # Janela: ativados entre 30 e 365 dias atrás (janela de 30d já encerrada)
    # Exclui id_assunto=313 na data de ativação (mesma regra do Power BI)
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT
                SUM(CASE WHEN com_contato=1 AND cancelou=1 THEN 1 ELSE 0 END) AS cc_cancel,
                SUM(CASE WHEN com_contato=1                THEN 1 ELSE 0 END) AS cc_total,
                SUM(CASE WHEN com_contato=0 AND cancelou=1 THEN 1 ELSE 0 END) AS sc_cancel,
                SUM(CASE WHEN com_contato=0                THEN 1 ELSE 0 END) AS sc_total
            FROM (
                SELECT cc.id,
                    CASE WHEN COUNT(t.id) > 0 THEN 1 ELSE 0 END AS com_contato,
                    CASE WHEN NULLIF(cc.data_cancelamento, '0001-01-01') IS NOT NULL
                              AND cc.data_cancelamento > cc.data_ativacao
                         THEN 1 ELSE 0 END AS cancelou
                FROM cliente_contrato cc
                JOIN cliente c ON c.id = cc.id_cliente
                {tj}
                WHERE cc.motivo_inclusao IN ('I','M')
                  AND cc.data_ativacao >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
                  AND cc.data_ativacao <= DATE_SUB(CURDATE(), INTERVAL 30 DAY)
                  AND cc.data_ativacao IS NOT NULL
                  AND c.razao NOT LIKE '%%TESTE%%'
                  AND NOT EXISTS (
                      SELECT 1 FROM su_oss_chamado os
                      WHERE os.id_cliente = cc.id_cliente
                        AND os.id_assunto = 313
                        AND DATE(os.data_abertura) = cc.data_ativacao
                  )
                GROUP BY cc.id, cc.id_cliente, cc.data_cancelamento, cc.data_ativacao
            ) sub
        """, jp)
        r = cur.fetchone()
        cc_t = int(r['cc_total'] or 0); cc_c = int(r['cc_cancel'] or 0)
        sc_t = int(r['sc_total'] or 0); sc_c = int(r['sc_cancel'] or 0)
        return jsonify({
            'com_contato_total': cc_t, 'com_contato_cancelou': cc_c,
            'sem_contato_total': sc_t, 'sem_contato_cancelou': sc_c,
            'taxa_churn_com': round(cc_c/cc_t*100,1) if cc_t else 0,
            'taxa_churn_sem': round(sc_c/sc_t*100,1) if sc_t else 0,
        })
    finally:
        cur.close(); conn.close()


@app.route('/api/tecnicos')
def api_tecnicos():
    janela = p_janela()
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT f.funcionario AS tecnico,
                   COUNT(DISTINCT cc.id_cliente) AS clientes,
                   COUNT(t.id) AS qtd
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            -- Técnico que fez a OS de instalação mais recente do cliente
            JOIN (
                SELECT oc.id_cliente,
                       SUBSTRING_INDEX(
                           GROUP_CONCAT(oc.id_tecnico ORDER BY oc.id DESC), ',', 1
                       ) AS id_tecnico
                FROM su_oss_chamado oc
                JOIN su_oss_assunto a ON a.id = oc.id_assunto
                WHERE (a.assunto LIKE '%%INSTALA%%INTERNET%%'
                       OR a.assunto = 'INSTALACAO INTERNET'
                       OR a.assunto = '0.1.1 INSTALACAO DE INTERNET')
                  AND oc.id_tecnico > 0
                GROUP BY oc.id_cliente
            ) inst ON inst.id_cliente = cc.id_cliente
            JOIN funcionarios f ON f.id = inst.id_tecnico
            {tj}
            WHERE {BASE_WHERE}
              AND t.id IS NOT NULL
            GROUP BY f.funcionario
            ORDER BY qtd DESC LIMIT 10
        """, jp + [janela])
        rows = cur.fetchall()
        return jsonify({
            'labels':   [r['tecnico'] for r in rows],
            'valores':  [r['qtd'] for r in rows],
            'clientes': [r['clientes'] for r in rows],
        })
    finally:
        cur.close(); conn.close()


@app.route('/api/resolucao-sla')
def api_resolucao_sla():
    janela = p_janela()
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT
                CASE
                    WHEN h <= 4   THEN '≤ 4h'
                    WHEN h <= 24  THEN '4–24h'
                    WHEN h <= 72  THEN '1–3 dias'
                    WHEN h <= 168 THEN '3–7 dias'
                    ELSE '> 7 dias'
                END AS faixa,
                COUNT(*) AS qtd
            FROM (
                SELECT TIMESTAMPDIFF(HOUR, t.data_criacao, t.data_ultima_alteracao) AS h
                FROM cliente_contrato cc
                JOIN cliente c ON c.id = cc.id_cliente
                {tj}
                WHERE {BASE_WHERE}
                  AND t.id IS NOT NULL
                  AND t.status = 'F'
                  AND t.data_ultima_alteracao IS NOT NULL
                  AND TIMESTAMPDIFF(HOUR, t.data_criacao, t.data_ultima_alteracao) >= 0
            ) sub
            GROUP BY faixa
            ORDER BY FIELD(faixa,'≤ 4h','4–24h','1–3 dias','3–7 dias','> 7 dias')
        """, jp + [janela])
        rows = cur.fetchall()
        return jsonify({'labels': [r['faixa'] for r in rows],
                        'valores': [r['qtd'] for r in rows]})
    finally:
        cur.close(); conn.close()


@app.route('/api/bairros')
def api_bairros():
    janela = p_janela()
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT TRIM(UPPER(cc.bairro)) AS bairro, COUNT(t.id) AS qtd
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            {tj}
            WHERE {BASE_WHERE}
              AND t.id IS NOT NULL
              AND cc.bairro IS NOT NULL AND cc.bairro <> ''
            GROUP BY TRIM(UPPER(cc.bairro))
            ORDER BY qtd DESC LIMIT 10
        """, jp + [janela])
        rows = cur.fetchall()
        return jsonify({'labels': [r['bairro'] for r in rows],
                        'valores': [r['qtd'] for r in rows]})
    finally:
        cur.close(); conn.close()


@app.route('/api/canais')
def api_canais():
    janela = p_janela()
    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join()
        cur.execute(f"""
            SELECT
                COALESCE(ca.descricao, 'Não informado') AS canal,
                COUNT(t.id) AS qtd
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            {tj}
            LEFT JOIN su_canal_atendimento ca ON ca.id = t.id_canal_atendimento
            WHERE {BASE_WHERE}
              AND t.id IS NOT NULL
            GROUP BY canal
            ORDER BY qtd DESC
        """, jp + [janela])
        rows = cur.fetchall()
        return jsonify({'labels': [r['canal'] for r in rows],
                        'valores': [r['qtd'] for r in rows]})
    finally:
        cur.close(); conn.close()


@app.route('/api/clientes/export')
def api_clientes_export():
    janela         = p_janela()
    so_contato     = request.args.get('so_contato', '0') == '1'
    busca          = request.args.get('busca', '').strip()
    filtro_assunto = request.args.get('assunto', '').strip()
    min_tickets    = max(0, int(request.args.get('min_tickets', 0) or 0))

    conn = get_db(); cur = conn.cursor(dictionary=True)
    try:
        tj, jp = build_ticket_join(filtro_assunto)
        extra_where = ""
        extra_p = []
        if busca:
            extra_where = " AND c.razao LIKE %s"
            extra_p = [f'%{busca}%']
        if min_tickets > 0:
            having = f"HAVING COUNT(t.id) >= {min_tickets}"
        elif so_contato:
            having = "HAVING COUNT(t.id) > 0"
        else:
            having = ""
        base_p = jp + [janela] + extra_p

        cur.execute(f"""
            SELECT cc.id AS contrato_id, cc.id_cliente,
                   c.razao AS nome, c.telefone_celular, c.fone,
                   cc.data_ativacao, cc.motivo_inclusao,
                   COUNT(t.id)                               AS total_contatos,
                   MIN(t.data_criacao)                       AS primeiro_contato,
                   DATEDIFF(MIN(t.data_criacao), cc.data_ativacao) AS dias_primeiro
            FROM cliente_contrato cc
            JOIN cliente c ON c.id = cc.id_cliente
            {tj}
            WHERE {BASE_WHERE}
            {extra_where}
            GROUP BY cc.id, cc.id_cliente, c.razao, c.telefone_celular,
                     c.fone, cc.data_ativacao, cc.motivo_inclusao
            {having}
            ORDER BY total_contatos DESC, cc.data_ativacao DESC
        """, base_p)
        rows = cur.fetchall()
    finally:
        cur.close(); conn.close()

    def fmt(v): return str(v) if v is not None else ''
    MOTIVO = {'I': 'Instalação', 'M': 'Mud. Endereço'}

    out = io.StringIO()
    w = csv.writer(out, delimiter=';')
    w.writerow(['ID Contrato','ID Cliente','Cliente','Telefone','Ativação',
                'Tipo','Total Tickets','1º Contato','Dias até 1º Contato'])
    for r in rows:
        w.writerow([
            r['contrato_id'], r['id_cliente'], r['nome'],
            r['telefone_celular'] or r['fone'] or '',
            fmt(r['data_ativacao']),
            MOTIVO.get(r['motivo_inclusao'], r['motivo_inclusao']),
            r['total_contatos'],
            fmt(r['primeiro_contato']),
            fmt(r['dias_primeiro']),
        ])

    from datetime import date
    filename = f"pos_ativacao_{date.today().isoformat()}.csv"
    return Response(
        '﻿' + out.getvalue(),  # BOM para Excel abrir UTF-8 corretamente
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="{filename}"'}
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5009, debug=False)
