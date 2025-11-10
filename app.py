import os
import json
import datetime
import hashlib
import uuid
from flask import Flask, jsonify, request
from flask_cors import CORS
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

# ======================================================================
# API BACKEND - CASAMENTO LAÍS & VITOR
# Versão: 1.0 (MVP)
# ======================================================================

load_dotenv()
app = Flask(__name__)
CORS(app) # Permite que seu index.html (frontend) converse com este backend

# --- CONFIGURAÇÃO: BANCO DE DADOS ---
DATABASE_URL = os.environ.get("DATABASE_URL")

# --- SIMULAÇÃO DE SESSÃO (Para MVP - Em produção, usar Redis ou JWT) ---
# Armazena tokens de admin ativos: { "token_uuid": admin_id }
ADMIN_SESSIONS = {}

def get_db_connection():
    """Abre uma conexão com o PostgreSQL."""
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        print(f"🔴 ERRO AO CONECTAR NO DB: {e}")
        return None

# ======================================================================
# 1. SETUP DO BANCO DE DADOS (Auto-Criação das Tabelas)
# ======================================================================
def setup_database():
    """Cria as tabelas necessárias se elas não existirem."""
    conn = get_db_connection()
    if not conn: return
    try:
        cur = conn.cursor()
        print("ℹ️  [DB] Verificando tabelas do casamento...")

        # 1. Tabela Admin
        cur.execute("""
            CREATE TABLE IF NOT EXISTS laisvitor_admin (
                id SERIAL PRIMARY KEY,
                username VARCHAR(50) UNIQUE NOT NULL,
                chave_admin_hash VARCHAR(256) NOT NULL
            );
        """)

        # 2. Tabela Convidados
        cur.execute("""
            CREATE TABLE IF NOT EXISTS laisvitor_convidados (
                id SERIAL PRIMARY KEY,
                admin_id INTEGER REFERENCES laisvitor_admin(id),
                codigo_convite VARCHAR(20) UNIQUE NOT NULL,
                nome_convidado VARCHAR(255) NOT NULL,
                status_rsvp VARCHAR(50) DEFAULT 'Pendente',
                qtd_adultos INTEGER,
                restricoes_alimentares TEXT,
                data_confirmacao TIMESTAMP
            );
        """)

        # 3. Tabela Presentes
        cur.execute("""
            CREATE TABLE IF NOT EXISTS laisvitor_presentes (
                id SERIAL PRIMARY KEY,
                admin_id INTEGER REFERENCES laisvitor_admin(id),
                nome_presente VARCHAR(100) NOT NULL,
                descricao TEXT,
                imagem_url VARCHAR(255),
                valor_cota DECIMAL(10, 2) NOT NULL,
                esta_ativo BOOLEAN DEFAULT TRUE
            );
        """)

        # 4. Tabela Depoimentos
        cur.execute("""
            CREATE TABLE IF NOT EXISTS laisvitor_depoimentos (
                id SERIAL PRIMARY KEY,
                convidado_id INTEGER REFERENCES laisvitor_convidados(id),
                mensagem TEXT NOT NULL,
                status_aprovacao VARCHAR(50) DEFAULT 'Pendente',
                data_criacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # --- SEED INICIAL (Opcional: Cria um admin padrão se não existir) ---
        # Usuário: admin | Senha: 123 (Hash SHA256 para '123')
        cur.execute("SELECT COUNT(*) FROM laisvitor_admin")
        if cur.fetchone()[0] == 0:
             hash_padrao = hashlib.sha256("123".encode()).hexdigest()
             cur.execute("INSERT INTO laisvitor_admin (username, chave_admin_hash) VALUES (%s, %s)", ('admin', hash_padrao))
             print("✅ [DB] Admin padrão criado (User: admin / Pass: 123)")

        conn.commit()
        print("✅ [DB] Tabelas verificadas/criadas com sucesso.")

    except Exception as e:
        print(f"🔴 ERRO NO SETUP DO DB: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

# ======================================================================
# 2. MIDDLEWARE & UTILITÁRIOS
# ======================================================================
def hash_password(password):
    """Gera hash SHA256 da senha."""
    return hashlib.sha256(password.encode()).hexdigest()

def check_auth(request):
    """Verifica se o request tem um token de admin válido."""
    token = request.headers.get('Authorization')
    if not token: return None
    # Remove 'Bearer ' se estiver presente
    token = token.replace('Bearer ', '')
    return ADMIN_SESSIONS.get(token) # Retorna admin_id ou None

# ======================================================================
# 3. ENDPOINTS - AUTENTICAÇÃO (ADMIN)
# ======================================================================
@app.route('/api/login_admin', methods=['POST'])
def login_admin():
    data = request.json or {}
    username = data.get('username')
    chave_admin = data.get('chave_admin')

    if not username or not chave_admin:
        return jsonify({"erro": "Credenciais incompletas"}), 400

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        pass_hash = hash_password(chave_admin)
        cur.execute("SELECT id FROM laisvitor_admin WHERE username = %s AND chave_admin_hash = %s", (username, pass_hash))
        admin = cur.fetchone()
        
        if admin:
            # Gera um token simples (UUID)
            token = str(uuid.uuid4())
            ADMIN_SESSIONS[token] = admin[0] # Salva na memória
            return jsonify({"mensagem": "Login realizado", "token": token, "admin_id": admin[0]})
        else:
            return jsonify({"erro": "Usuário ou chave inválidos"}), 401
    finally:
        if conn: conn.close()

# ======================================================================
# 4. ENDPOINTS - CONVIDADOS (RSVP PÚBLICO)
# ======================================================================
@app.route('/api/rsvp/verificar', methods=['POST'])
def rsvp_verificar():
    """LIA usa isso para checar se o código do convite existe."""
    data = request.json or {}
    codigo = data.get('codigo_convite')

    conn = get_db_connection()
    try:
        # Usa RealDictCursor para retornar dicionário em vez de tupla
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT id, nome_convidado, status_rsvp FROM laisvitor_convidados WHERE codigo_convite = %s", (codigo,))
        convidado = cur.fetchone()

        if convidado:
            return jsonify(convidado)
        else:
            return jsonify({"erro": "Código de convite não encontrado"}), 404
    finally:
        if conn: conn.close()

@app.route('/api/rsvp/confirmar', methods=['POST'])
def rsvp_confirmar():
    """LIA usa isso para salvar a confirmação."""
    data = request.json or {}
    codigo = data.get('codigo_convite')
    status = data.get('status_rsvp') # 'Confirmado' ou 'Recusado'
    qtd_adultos = data.get('qtd_adultos', 0)
    restricoes = data.get('restricoes_alimentares', '')

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            UPDATE laisvitor_convidados 
            SET status_rsvp = %s, qtd_adultos = %s, restricoes_alimentares = %s, data_confirmacao = NOW()
            WHERE codigo_convite = %s
            RETURNING id
        """, (status, qtd_adultos, restricoes, codigo))
        
        if cur.fetchone():
            conn.commit()
            return jsonify({"mensagem": "RSVP atualizado com sucesso!"})
        else:
            return jsonify({"erro": "Código inválido para atualização"}), 404
    finally:
        if conn: conn.close()

# ======================================================================
# 5. ENDPOINTS - DEPOIMENTOS (PÚBLICO)
# ======================================================================
@app.route('/api/depoimentos', methods=['GET'])
def get_depoimentos_publico():
    """Retorna APENAS os depoimentos 'Aprovado' para o carrossel."""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        # Faz JOIN para pegar o nome do convidado também
        cur.execute("""
            SELECT d.mensagem as texto, c.nome_convidado as nome, TO_CHAR(d.data_criacao, 'DD/MM/YYYY') as data
            FROM laisvitor_depoimentos d
            JOIN laisvitor_convidados c ON d.convidado_id = c.id
            WHERE d.status_aprovacao = 'Aprovado'
            ORDER BY d.data_criacao DESC
        """)
        depoimentos = cur.fetchall()
        return jsonify(depoimentos)
    finally:
        if conn: conn.close()

@app.route('/api/depoimentos', methods=['POST'])
def post_depoimento_publico():
    """Salva um novo depoimento como 'Pendente'."""
    data = request.json or {}
    codigo = data.get('codigo_convite')
    mensagem = data.get('mensagem')

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # 1. Acha o ID do convidado pelo código
        cur.execute("SELECT id FROM laisvitor_convidados WHERE codigo_convite = %s", (codigo,))
        res = cur.fetchone()
        if not res:
            return jsonify({"erro": "Código inválido"}), 404
        convidado_id = res[0]

        # 2. Insere o depoimento
        cur.execute("INSERT INTO laisvitor_depoimentos (convidado_id, mensagem, status_aprovacao) VALUES (%s, %s, 'Pendente')", (convidado_id, mensagem))
        conn.commit()
        return jsonify({"mensagem": "Depoimento enviado para aprovação!"})
    finally:
        if conn: conn.close()

# ======================================================================
# 6. ENDPOINTS - PRESENTES (PÚBLICO)
# ======================================================================
@app.route('/api/presentes', methods=['GET'])
def get_presentes_publico():
    """Lista os presentes ativos para a página 'presentes.html'."""
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM laisvitor_presentes WHERE esta_ativo = TRUE ORDER BY id")
        presentes = cur.fetchall()
        # Converte DECIMAL para float para o JSON não quebrar
        for p in presentes:
            p['valor_cota'] = float(p['valor_cota'])
        return jsonify(presentes)
    finally:
        if conn: conn.close()

# ======================================================================
# 7. ENDPOINTS - ADMIN (PROTEGIDOS)
# ======================================================================

# --- 7.1 Dashboard Stats ---
@app.route('/api/admin/dashboard_stats', methods=['GET'])
def admin_stats():
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Contagens rápidas
        cur.execute("SELECT COUNT(*) FROM laisvitor_convidados WHERE status_rsvp = 'Confirmado'")
        confirmados = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM laisvitor_convidados WHERE status_rsvp = 'Pendente'")
        pendentes = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM laisvitor_depoimentos WHERE status_aprovacao = 'Pendente'")
        depoimentos_pendentes = cur.fetchone()[0]
        
        return jsonify({
            "confirmados": confirmados,
            "pendentes_rsvp": pendentes,
            "recados_moderacao": depoimentos_pendentes
        })
    finally:
        if conn: conn.close()

# --- 7.2 Moderação de Depoimentos ---
@app.route('/api/admin/depoimentos/pendentes', methods=['GET'])
def admin_get_depoimentos_pendentes():
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    conn = get_db_connection()
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("""
            SELECT d.id, d.mensagem, c.nome_convidado
            FROM laisvitor_depoimentos d
            JOIN laisvitor_convidados c ON d.convidado_id = c.id
            WHERE d.status_aprovacao = 'Pendente'
        """)
        return jsonify(cur.fetchall())
    finally:
        if conn: conn.close()

@app.route('/api/admin/depoimentos/<int:id>/status', methods=['PUT'])
def admin_update_depoimento_status(id):
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    data = request.json or {}
    novo_status = data.get('status') # 'Aprovado' ou 'Rejeitado'

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE laisvitor_depoimentos SET status_aprovacao = %s WHERE id = %s", (novo_status, id))
        conn.commit()
        return jsonify({"mensagem": f"Depoimento {id} atualizado para {novo_status}"})
    finally:
        if conn: conn.close()

# --- 7.3 Gerenciamento de Convidados (Básico para começar) ---
@app.route('/api/admin/convidados', methods=['POST'])
def admin_add_convidado():
    """Adiciona um novo convidado à lista (Gera o código)."""
    if not check_auth(request): return jsonify({"erro": "Não autorizado"}), 403
    data = request.json or {}
    nome = data.get('nome_convidado')
    # Gera um código aleatório de 6 dígitos (ex: A7B9K2)
    codigo = str(uuid.uuid4())[:6].upper()

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        # Assume admin_id=1 para MVP, ou pega da sessão se tiver múltiplos
        cur.execute("INSERT INTO laisvitor_convidados (admin_id, nome_convidado, codigo_convite) VALUES (1, %s, %s) RETURNING id, codigo_convite", (nome, codigo))
        res = cur.fetchone()
        conn.commit()
        return jsonify({"mensagem": "Convidado criado", "id": res[0], "codigo": res[1]})
    finally:
        if conn: conn.close()

# ======================================================================
# INICIALIZAÇÃO
# ======================================================================
if __name__ == '__main__':
    # Tenta configurar o DB na inicialização local
    setup_database()
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port, debug=True)