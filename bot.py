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
import base64
from datetime import datetime, timedelta
from threading import Thread, Lock
from urllib.parse import urlparse

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
    
    # Tabla de proxies (ACTUALIZADA para soportar más campos)
    cursor.execute('''CREATE TABLE IF NOT EXISTS proxies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        proxy TEXT UNIQUE,
        ip TEXT,
        puerto INTEGER,
        usuario TEXT,
        password TEXT,
        protocolo TEXT DEFAULT 'http',
        fecha TEXT,
        successes INTEGER DEFAULT 0,
        failures INTEGER DEFAULT 0,
        last_used TEXT,
        last_test TEXT,
        status TEXT DEFAULT 'untested',
        velocidad REAL,
        ultimo_test TEXT
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
        cursor.execute("ALTER TABLE proxies ADD COLUMN ip TEXT")
        cursor.execute("ALTER TABLE proxies ADD COLUMN puerto INTEGER")
        cursor.execute("ALTER TABLE proxies ADD COLUMN usuario TEXT")
        cursor.execute("ALTER TABLE proxies ADD COLUMN password TEXT")
        cursor.execute("ALTER TABLE proxies ADD COLUMN protocolo TEXT DEFAULT 'http'")
        cursor.execute("ALTER TABLE proxies ADD COLUMN velocidad REAL")
    except:
        pass
    
    conn.commit()
    conn.close()
    print("✅ Base de datos configurada correctamente")

# Inicializar BD
init_database()

# Cola de tareas masivas
active_tasks = {}

# ==================== CLASE PARA MANEJAR PROXIES ====================

class ProxyParser:
    """
    Clase para parsear y manejar diferentes formatos de proxy
    Soporta TODOS los formatos existentes
    """
    
    FORMATOS = {
        'ip:puerto': r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$',
        'ip:puerto:user:pass': r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+):([^:]+):([^:]+)$',
        'user:pass@ip:puerto': r'^([^:]+):([^@]+)@(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$',
        'protocolo://user:pass@ip:puerto': r'^(https?|socks4|socks5)://([^:]+):([^@]+)@(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$',
        'protocolo://ip:puerto': r'^(https?|socks4|socks5)://(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$'
    }
    
    @staticmethod
    def parse(proxy_string):
        """
        Parsea cualquier formato de proxy y devuelve un diccionario con los componentes
        """
        proxy_string = proxy_string.strip()
        
        for formato, patron in ProxyParser.FORMATOS.items():
            match = re.match(patron, proxy_string)
            if match:
                grupos = match.groups()
                
                if formato == 'ip:puerto':
                    return {
                        'protocolo': 'http',
                        'ip': grupos[0],
                        'puerto': int(grupos[1]),
                        'usuario': None,
                        'password': None,
                        'formato': formato,
                        'string_original': proxy_string
                    }
                    
                elif formato == 'ip:puerto:user:pass':
                    return {
                        'protocolo': 'http',
                        'ip': grupos[0],
                        'puerto': int(grupos[1]),
                        'usuario': grupos[2],
                        'password': grupos[3],
                        'formato': formato,
                        'string_original': proxy_string
                    }
                    
                elif formato == 'user:pass@ip:puerto':
                    return {
                        'protocolo': 'http',
                        'ip': grupos[2],
                        'puerto': int(grupos[3]),
                        'usuario': grupos[0],
                        'password': grupos[1],
                        'formato': formato,
                        'string_original': proxy_string
                    }
                    
                elif formato == 'protocolo://user:pass@ip:puerto':
                    return {
                        'protocolo': grupos[0],
                        'ip': grupos[3],
                        'puerto': int(grupos[4]),
                        'usuario': grupos[1],
                        'password': grupos[2],
                        'formato': formato,
                        'string_original': proxy_string
                    }
                    
                elif formato == 'protocolo://ip:puerto':
                    return {
                        'protocolo': grupos[0],
                        'ip': grupos[1],
                        'puerto': int(grupos[2]),
                        'usuario': None,
                        'password': None,
                        'formato': formato,
                        'string_original': proxy_string
                    }
        
        return None
    
    @staticmethod
    def to_requests_dict(proxy_info):
        """
        Convierte la información del proxy a un diccionario para requests
        """
        if proxy_info['usuario'] and proxy_info['password']:
            auth = f"{proxy_info['usuario']}:{proxy_info['password']}@"
        else:
            auth = ""
        
        proxy_url = f"{proxy_info['protocolo']}://{auth}{proxy_info['ip']}:{proxy_info['puerto']}"
        
        return {
            'http': proxy_url,
            'https': proxy_url.replace('http://', 'https://') if proxy_info['protocolo'] == 'http' else proxy_url
        }
    
    @staticmethod
    def to_curl_format(proxy_info):
        """Convierte a formato para curl"""
        if proxy_info['usuario'] and proxy_info['password']:
            return f"{proxy_info['usuario']}:{proxy_info['password']}@{proxy_info['ip']}:{proxy_info['puerto']}"
        else:
            return f"{proxy_info['ip']}:{proxy_info['puerto']}"
    
    @staticmethod
    def normalizar(proxy_string):
        """Normaliza cualquier formato a ip:puerto:user:pass (para guardar en BD)"""
        info = ProxyParser.parse(proxy_string)
        if not info:
            return None
        
        if info['usuario'] and info['password']:
            return f"{info['ip']}:{info['puerto']}:{info['usuario']}:{info['password']}"
        else:
            return f"{info['ip']}:{info['puerto']}"

# ==================== TESTER DE PROXIES MEJORADO ====================

class ProxyTester:
    """
    Clase para testear proxies con múltiples métodos y diagnósticos
    """
    
    def __init__(self):
        self.endpoints = [
            {'url': 'http://httpbin.org/ip', 'name': 'HTTPBin HTTP', 'timeout': 5},
            {'url': 'https://httpbin.org/ip', 'name': 'HTTPBin HTTPS', 'timeout': 5},
            {'url': 'http://ip-api.com/json', 'name': 'IP-API', 'timeout': 5},
            {'url': 'https://api.ipify.org', 'name': 'IPify', 'timeout': 5},
            {'url': 'http://checkip.amazonaws.com', 'name': 'AWS Check', 'timeout': 5}
        ]
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
    
    def test_socket(self, proxy_info, timeout=3):
        """Test de socket básico"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            start = time.time()
            result = sock.connect_ex((proxy_info['ip'], proxy_info['puerto']))
            elapsed = time.time() - start
            sock.close()
            
            if result == 0:
                return True, f"Socket OK ({elapsed:.2f}s)", elapsed
            else:
                errores = {
                    111: "Connection refused",
                    110: "Timeout",
                    113: "No route to host",
                    101: "Network unreachable"
                }
                return False, errores.get(result, f"Error {result}"), elapsed
                
        except Exception as e:
            return False, str(e), 0
    
    def test_http(self, proxy_info, timeout=5):
        """Test HTTP completo con múltiples endpoints"""
        
        proxy_dict = ProxyParser.to_requests_dict(proxy_info)
        
        resultados = []
        
        for endpoint in self.endpoints:
            try:
                start = time.time()
                response = requests.get(
                    endpoint['url'],
                    proxies=proxy_dict,
                    headers=self.headers,
                    timeout=timeout,
                    verify=False
                )
                elapsed = time.time() - start
                
                if response.status_code == 200:
                    try:
                        data = response.json()
                        ip_visible = data.get('ip') or data.get('origin') or data.get('ip_address')
                    except:
                        ip_visible = response.text.strip()
                    
                    resultados.append({
                        'endpoint': endpoint['name'],
                        'success': True,
                        'tiempo': elapsed,
                        'ip_visible': ip_visible
                    })
                else:
                    resultados.append({
                        'endpoint': endpoint['name'],
                        'success': False,
                        'error': f"HTTP {response.status_code}",
                        'tiempo': elapsed
                    })
                    
            except requests.exceptions.ConnectTimeout:
                resultados.append({
                    'endpoint': endpoint['name'],
                    'success': False,
                    'error': 'Timeout',
                    'tiempo': timeout
                })
            except requests.exceptions.ProxyError as e:
                resultados.append({
                    'endpoint': endpoint['name'],
                    'success': False,
                    'error': 'Proxy Error',
                    'detalle': str(e)[:50]
                })
            except requests.exceptions.SSLError:
                resultados.append({
                    'endpoint': endpoint['name'],
                    'success': False,
                    'error': 'SSL Error'
                })
            except Exception as e:
                resultados.append({
                    'endpoint': endpoint['name'],
                    'success': False,
                    'error': str(e)[:30]
                })
        
        return resultados
    
    def test_completo(self, proxy_string):
        """Test completo con diagnóstico"""
        
        proxy_info = ProxyParser.parse(proxy_string)
        if not proxy_info:
            return {'error': 'Formato de proxy inválido'}
        
        resultados = {
            'proxy': proxy_string,
            'info': proxy_info,
            'socket': None,
            'http': None,
            'conclusion': None
        }
        
        # Test de socket
        socket_ok, socket_msg, socket_time = self.test_socket(proxy_info)
        resultados['socket'] = {
            'ok': socket_ok,
            'msg': socket_msg,
            'tiempo': socket_time
        }
        
        if not socket_ok:
            resultados['conclusion'] = '❌ MUERTO (socket falla)'
            return resultados
        
        # Test HTTP
        http_resultados = self.test_http(proxy_info)
        resultados['http'] = http_resultados
        
        # Analizar resultados HTTP
        exitosos = sum(1 for r in http_resultados if r['success'])
        total = len(http_resultados)
        
        if exitosos == 0:
            resultados['conclusion'] = '❌ MUERTO (HTTP falla)'
        elif exitosos == total:
            tiempos = [r['tiempo'] for r in http_resultados if r['success']]
            promedio = sum(tiempos) / len(tiempos)
            
            if promedio < 1:
                resultados['conclusion'] = f'✅ EXCELENTE ({promedio:.2f}s promedio)'
            elif promedio < 3:
                resultados['conclusion'] = f'👍 BUENO ({promedio:.2f}s promedio)'
            else:
                resultados['conclusion'] = f'🐢 LENTO ({promedio:.2f}s promedio)'
        else:
            resultados['conclusion'] = f'⚠️ PARCIAL ({exitosos}/{total} endpoints OK)'
        
        return resultados

# Instanciar tester
tester = ProxyTester()

# ==================== FUNCIONES DE PROXIES ====================

def guardar_proxy(proxy_string):
    """Guarda un proxy en cualquier formato"""
    
    proxy_info = ProxyParser.parse(proxy_string)
    if not proxy_info:
        return False, "Formato de proxy inválido. Usa /formatos para ver los formatos soportados"
    
    # Normalizar para guardar
    proxy_normalizado = ProxyParser.normalizar(proxy_string)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO proxies 
            (proxy, ip, puerto, usuario, password, protocolo, fecha) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            proxy_normalizado,
            proxy_info['ip'],
            proxy_info['puerto'],
            proxy_info['usuario'],
            proxy_info['password'],
            proxy_info['protocolo'],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        return True, f"✅ Proxy guardado correctamente"
    except sqlite3.IntegrityError:
        return False, "❌ El proxy ya existe"
    except Exception as e:
        return False, f"❌ Error: {str(e)}"
    finally:
        conn.close()

def guardar_proxies_desde_texto(texto):
    """Guarda proxies desde un archivo de texto (soporta todos los formatos)"""
    lineas = texto.strip().split('\n')
    guardados = 0
    repetidos = 0
    invalidos = 0
    errores = []
    
    for linea in lineas:
        linea = linea.strip()
        if not linea or linea.startswith('#'):
            continue
        
        success, msg = guardar_proxy(linea)
        if success:
            guardados += 1
        else:
            if "ya existe" in msg:
                repetidos += 1
            else:
                invalidos += 1
                errores.append(f"{linea[:30]}... - {msg}")
    
    return guardados, repetidos, invalidos, errores

def obtener_proxies():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT proxy FROM proxies")
    proxies = [row[0] for row in cursor.fetchall()]
    conn.close()
    return proxies

def obtener_proxies_con_estadisticas():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT proxy, ip, puerto, usuario, protocolo, successes, failures, status, velocidad, last_test 
        FROM proxies 
        ORDER BY successes DESC, failures ASC
    """)
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

def actualizar_estadisticas_proxy(proxy, success):
    conn = get_db_connection()
    cursor = conn.cursor()
    campo = "successes" if success else "failures"
    cursor.execute(f"UPDATE proxies SET {campo} = {campo} + 1, last_used = ? WHERE proxy = ?", 
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), proxy))
    conn.commit()
    conn.close()

def actualizar_status_proxy(proxy, status, velocidad, detalle):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE proxies 
        SET status = ?, velocidad = ?, last_test = ? 
        WHERE proxy = ?
    """, (status, velocidad, detalle, proxy))
    conn.commit()
    conn.close()

# ==================== FUNCIONES DE SITIOS SHOPIFY ====================

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

def guardar_sitios_desde_texto(texto):
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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT url FROM sitios")
    sitios = [row[0] for row in cursor.fetchall()]
    conn.close()
    return sitios

def eliminar_sitio(url):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sitios WHERE url = ?", (url,))
    conn.commit()
    filas = cursor.rowcount
    conn.close()
    return filas > 0

def actualizar_estadisticas_sitio(url, success):
    conn = get_db_connection()
    cursor = conn.cursor()
    campo = "successes" if success else "failures"
    cursor.execute(f"UPDATE sitios SET {campo} = {campo} + 1, last_used = ? WHERE url = ?", 
                  (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), url))
    conn.commit()
    conn.close()

# ==================== FUNCIONES DE TARJETAS ====================

def guardar_tarjetas_desde_texto(texto):
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
        'tarjetas': total_tarjetas or 0,
        'checks': total_checks or 0,
        'aprobadas': total_success or 0
    }

# ==================== FUNCIÓN DE CONSULTA BIN ====================

def consultar_bin(bin_number):
    """Consulta información de BIN con múltiples fuentes"""
    try:
        bin_number = bin_number[:6]
        
        # Intentar con bincheck.io primero
        url = f"https://lookup.bincheck.io/api/v2/{bin_number}"
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }
        
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            return {
                'scheme': data.get('scheme', 'UNKNOWN').upper(),
                'type': data.get('type', 'UNKNOWN').upper(),
                'country': {
                    'name': data.get('country', {}).get('name', 'Unknown'),
                    'emoji': data.get('country', {}).get('emoji', '🌍')
                },
                'bank': {
                    'name': data.get('bank', {}).get('name', 'Unknown')
                }
            }
        
        # Fallback a binlist.net
        url = f"https://lookup.binlist.net/{bin_number}"
        headers = {'Accept-Version': '3', 'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=5)
        
        if response.status_code == 200:
            data = response.json()
            return {
                'scheme': data.get('scheme', 'UNKNOWN').upper(),
                'type': data.get('type', 'UNKNOWN').upper(),
                'country': {
                    'name': data.get('country', {}).get('name', 'Unknown'),
                    'emoji': data.get('country', {}).get('emoji', '🌍')
                },
                'bank': {
                    'name': data.get('bank', {}).get('name', 'Unknown')
                }
            }
        
        return {"error": "BIN no encontrado", "bin": bin_number}
        
    except Exception as e:
        return {"error": str(e), "bin": bin_number}

# ==================== FUNCIONES DE VERIFICACIÓN STRIPE ====================

def verificar_api_stripe(cc, proxy=None):
    """Verifica usando Stripe (endpoint /api/check3) - $1.00"""
    try:
        api_url = f"https://samurai-api-hub.up.railway.app/api/check3?c={cc}"
        if proxy:
            # Obtener información del proxy si es necesario
            proxy_info = ProxyParser.parse(proxy)
            if proxy_info:
                proxy_dict = ProxyParser.to_requests_dict(proxy_info)
                # Aquí iría la lógica para usar el proxy con la API
                pass
        
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
    """Verifica usando PayPal con diferentes montos"""
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
    """Verifica usando AutoShopify (endpoint shopi.php)"""
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
                'message': f'HTTP {response.status_code}',
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
    """Formato premium con diseño tipo checker profesional"""
    
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
        
        country_info = bin_info.get('country', {})
        country_name = country_info.get('name', 'Unknown')
        country_emoji = country_info.get('emoji', '🌍')
        
        bank_info = bin_info.get('bank', {})
        bank_name = bank_info.get('name', 'Unknown')
        
        if scheme == "VISA":
            tipo_completo = "VISA"
        elif scheme == "MASTERCARD":
            tipo_completo = "MASTERCARD"
        elif scheme == "AMEX":
            tipo_completo = "AMERICAN EXPRESS"
        else:
            tipo_completo = scheme
        
        tipo_especifico = card_type.capitalize() if card_type else "UNKNOWN"
        country_line = f"{country_name} {country_emoji}"
    else:
        tipo_completo = "UNKNOWN"
        tipo_especifico = "UNKNOWN"
        country_line = "Unknown 🌍"
        bank_name = "Unknown"
    
    proxy_status = "API 🌐" if resultado_api['proxy'] == 'gestionado' else "Live ✨"
    
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

# ==================== SISTEMA DE KEYS ====================

def generar_key(longitud=16):
    caracteres = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
    key = ''.join(random.choices(caracteres, k=longitud))
    return '-'.join([key[i:i+4] for i in range(0, len(key), 4)])

def crear_key(duracion_dias=30, max_uses=1, notas=""):
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
    
    expires = datetime.strptime(key_info['expires_date'], "%Y-%m-%d %H:%M:%S")
    if expires < datetime.now():
        return False, "❌ Key expirada"
    
    if key_info['uses_count'] >= key_info['max_uses']:
        return False, "❌ Key alcanzó su límite de usos"
    
    return True, key_info

def registrar_uso_key(key, user_id, username):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute(
        "UPDATE access_keys SET uses_count = uses_count + 1, last_used = ? WHERE key = ?",
        (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), key)
    )
    
    cursor.execute("""
        INSERT OR REPLACE INTO authorized_users 
        (user_id, username, key_used, first_seen, last_seen, uses_count) 
        VALUES (?, ?, ?, 
                COALESCE((SELECT first_seen FROM authorized_users WHERE user_id = ?), ?), 
                ?, 
                COALESCE((SELECT uses_count FROM authorized_users WHERE user_id = ?), 0) + 1)
    """, (
        user_id, username, key, 
        user_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 
        user_id
    ))
    
    conn.commit()
    conn.close()

def verificar_acceso(message):
    user_id = message.from_user.id
    
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

def requiere_acceso(func):
    def wrapper(message, *args, **kwargs):
        tiene_acceso, mensaje = verificar_acceso(message)
        if tiene_acceso:
            return func(message, *args, **kwargs)
        else:
            bot.reply_to(message, mensaje)
            return None
    return wrapper

# ==================== COMANDOS DE KEYS ====================

@bot.message_handler(commands=['genkey'])
def cmd_genkey(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ Solo el owner puede generar keys")
        return
    
    try:
        partes = message.text.split()
        dias = int(partes[1]) if len(partes) > 1 else 30
        usos = int(partes[2]) if len(partes) > 2 else 1
        notas = ' '.join(partes[3:]) if len(partes) > 3 else ""
        
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

@bot.message_handler(commands=['key'])
def cmd_activate_key(message):
    try:
        key = message.text.split()[1]
        user_id = message.from_user.id
        username = message.from_user.username or "unknown"
        
        es_valida, info = validar_key(key)
        
        if es_valida:
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

@bot.message_handler(commands=['listkeys'])
def cmd_listkeys(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "❌ Solo el owner puede ver las keys")
        return
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM access_keys ORDER BY created_date DESC")
    keys = cursor.fetchall()
    conn.close()
    
    if not keys:
        bot.reply_to(message, "📭 No hay keys generadas")
        return
    
    texto = "🔑 *KEYS GENERADAS*\n\n"
    
    for k in keys:
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

# ==================== NUEVOS COMANDOS DE PROXY ====================

@bot.message_handler(commands=['formatos'])
@requiere_acceso
def cmd_formatos(message):
    texto = (
        "📋 *FORMATOS DE PROXY SOPORTADOS*\n\n"
        "1️⃣ `ip:puerto`\n"
        "   Ej: `62.60.131.197:5678`\n\n"
        "2️⃣ `ip:puerto:usuario:contraseña`\n"
        "   Ej: `193.36.187.170:3128:user:pass`\n\n"
        "3️⃣ `usuario:contraseña@ip:puerto`\n"
        "   Ej: `user:pass@193.36.187.170:3128`\n\n"
        "4️⃣ `protocolo://ip:puerto`\n"
        "   Ej: `http://62.60.131.197:5678`\n"
        "   Ej: `socks5://62.60.131.197:5678`\n\n"
        "5️⃣ `protocolo://user:pass@ip:puerto`\n"
        "   Ej: `http://user:pass@193.36.187.170:3128`\n\n"
        "💡 *Todos son válidos y se normalizan automáticamente*"
    )
    bot.send_message(message.chat.id, texto, parse_mode='Markdown')

@bot.message_handler(commands=['testone'])
@requiere_acceso
def cmd_test_one(message):
    """Prueba un proxy específico"""
    try:
        proxy_string = message.text.split(' ', 1)[1]
        
        msg = bot.reply_to(message, f"🔬 Testeando `{proxy_string}`...", parse_mode='Markdown')
        
        resultados = tester.test_completo(proxy_string)
        
        if 'error' in resultados:
            bot.edit_message_text(f"❌ {resultados['error']}", msg.chat.id, msg.message_id)
            return
        
        texto = f"🔬 *RESULTADO DEL TEST*\n"
        texto += f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        texto += f"📌 Proxy: `{proxy_string}`\n"
        texto += f"📋 Formato: {resultados['info']['formato']}\n"
        texto += f"🌐 IP: {resultados['info']['ip']}\n"
        texto += f"🔌 Puerto: {resultados['info']['puerto']}\n"
        
        if resultados['info']['usuario']:
            texto += f"🔑 Usuario: {resultados['info']['usuario']}\n"
        
        texto += f"\n📡 *SOCKET:* {resultados['socket']['msg']}\n"
        
        if resultados['http']:
            texto += f"\n🌐 *TEST HTTP:*\n"
            exitosos = 0
            for r in resultados['http']:
                if r['success']:
                    exitosos += 1
                    texto += f"  ✅ {r['endpoint']}: {r['tiempo']:.2f}s\n"
                    if 'ip_visible' in r:
                        texto += f"     └ IP: {r['ip_visible']}\n"
                else:
                    texto += f"  ❌ {r['endpoint']}: {r.get('error', 'Error')}\n"
        
        texto += f"\n📊 *CONCLUSIÓN:* {resultados['conclusion']}"
        
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode='Markdown')
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /testone ip:puerto:user:pass")

# ==================== COMANDOS DE PROXIES ====================

@bot.message_handler(commands=['addproxy'])
@requiere_acceso
def cmd_add_proxy(message):
    try:
        proxy_string = message.text.split(' ', 1)[1]
        
        proxy_info = ProxyParser.parse(proxy_string)
        if not proxy_info:
            bot.reply_to(message, 
                "❌ *Formato inválido*\n"
                "Usa /formatos para ver los formatos soportados",
                parse_mode='Markdown'
            )
            return
        
        success, msg = guardar_proxy(proxy_string)
        
        if success:
            texto = (
                f"✅ *PROXY GUARDADO*\n\n"
                f"📌 Original: `{proxy_string}`\n"
                f"📌 Normalizado: `{ProxyParser.normalizar(proxy_string)}`\n"
                f"🌐 IP: {proxy_info['ip']}\n"
                f"🔌 Puerto: {proxy_info['puerto']}\n"
                f"🔑 Auth: {'Sí' if proxy_info['usuario'] else 'No'}\n"
                f"📡 Protocolo: {proxy_info['protocolo']}"
            )
        else:
            texto = f"❌ *Error*\n{msg}"
        
        bot.reply_to(message, texto, parse_mode='Markdown')
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /addproxy ip:puerto:user:pass")

@bot.message_handler(commands=['delproxy'])
@requiere_acceso
def cmd_del_proxy(message):
    try:
        proxy = message.text.split(' ', 1)[1]
        if eliminar_proxy(proxy):
            bot.reply_to(message, f"✅ Proxy eliminado")
        else:
            bot.reply_to(message, "❌ Proxy no encontrado")
    except IndexError:
        bot.reply_to(message, "❌ Uso: /delproxy ip:puerto:user:pass")

@bot.message_handler(commands=['delallproxy'])
@requiere_acceso
def cmd_del_all_proxies(message):
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
    proxies = obtener_proxies_con_estadisticas()
    
    if not proxies:
        bot.send_message(message.chat.id, "📭 No hay proxies guardados")
        return
    
    texto = "╔══════════════════════════════════════╗\n"
    texto += "║         🌐 MIS PROXIES              ║\n"
    texto += "╠══════════════════════════════════════╣\n"
    
    for p in proxies[:15]:
        proxy, ip, puerto, usuario, protocolo, succ, fail, status, velocidad, last_test = p
        
        if 'EXCELENTE' in str(status):
            emoji = "✅"
        elif 'BUENO' in str(status):
            emoji = "👍"
        elif 'LENTO' in str(status):
            emoji = "🐢"
        elif 'PARCIAL' in str(status):
            emoji = "⚠️"
        else:
            emoji = "❌"
        
        proxy_short = proxy[:30] + "..." if len(proxy) > 30 else proxy
        
        texto += f"║ {emoji} {proxy_short:<34} ║\n"
        texto += f"║    ├─ IP: {ip:<15} Puerto: {puerto:<5} ║\n"
        
        if usuario:
            texto += f"║    ├─ Auth: Sí                          ║\n"
        
        texto += f"║    ├─ ✅ {succ}  ❌ {fail}                    ║\n"
        
        if velocidad:
            texto += f"║    └─ ⚡ {velocidad:.2f}s                        ║\n"
    
    texto += "╚══════════════════════════════════════╝"
    
    bot.send_message(message.chat.id, texto)

@bot.message_handler(commands=['testproxy'])
@requiere_acceso
def cmd_test_proxies(message):
    """Testea todos los proxies guardados"""
    
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
        if i % 3 == 0 or i == len(proxies):
            try:
                bot.edit_message_text(
                    f"🔬 Testeando {i}/{len(proxies)}...",
                    msg.chat.id,
                    msg.message_id
                )
            except:
                pass
        
        test_result = tester.test_completo(proxy)
        
        if 'error' in test_result:
            resultados['muerto'].append((proxy, "Error de formato"))
            continue
        
        conclusion = test_result['conclusion']
        
        if 'EXCELENTE' in conclusion:
            resultados['excelente'].append((proxy, conclusion))
        elif 'BUENO' in conclusion:
            resultados['bueno'].append((proxy, conclusion))
        elif 'LENTO' in conclusion:
            resultados['lento'].append((proxy, conclusion))
        elif 'PARCIAL' in conclusion:
            resultados['parcial'].append((proxy, conclusion))
        else:
            resultados['muerto'].append((proxy, conclusion))
    
    texto = f"""🔬 *TEST DE PROXIES COMPLETADO*
━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 *RESULTADOS:*

✅ Excelentes: {len(resultados['excelente'])}
👍 Buenos: {len(resultados['bueno'])}
🐢 Lentos: {len(resultados['lento'])}
⚠️ Parciales: {len(resultados['parcial'])}
❌ Muertos: {len(resultados['muerto'])}

📁 Se generó archivo con detalles"""
    
    filename = f"proxy_test_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("🔬 RESULTADOS DETALLADOS\n")
        f.write("━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n")
        
        for cat, items in [
            ('EXCELENTES', resultados['excelente']),
            ('BUENOS', resultados['bueno']),
            ('LENTOS', resultados['lento']),
            ('PARCIALES', resultados['parcial']),
            ('MUERTOS', resultados['muerto'])
        ]:
            if items:
                f.write(f"\n{cat} ({len(items)}):\n")
                for proxy, detalle in items:
                    f.write(f"  • {proxy}\n")
                    f.write(f"    └ {detalle}\n")
    
    try:
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode='Markdown')
    except:
        bot.send_message(msg.chat.id, texto, parse_mode='Markdown')
    
    with open(filename, 'rb') as f:
        bot.send_document(msg.chat.id, f, caption="📊 Resultados detallados")
    
    os.remove(filename)

@bot.message_handler(commands=['px'])
@requiere_acceso
def cmd_px_rapido(message):
    """Test rápido usando el método HTTP (como curl)"""
    
    proxies = obtener_proxies()
    
    if not proxies:
        bot.reply_to(message, "📭 No hay proxies guardados")
        return
    
    msg = bot.reply_to(message, f"🔄 Test rápido de {len(proxies)} proxies...")
    
    vivos = 0
    muertos = 0
    detalles = []
    
    for i, proxy in enumerate(proxies, 1):
        if i % 3 == 0 or i == len(proxies):
            try:
                bot.edit_message_text(
                    f"🔄 Testeando {i}/{len(proxies)}...",
                    msg.chat.id,
                    msg.message_id
                )
            except:
                pass
        
        try:
            proxy_info = ProxyParser.parse(proxy)
            if not proxy_info:
                muertos += 1
                detalles.append(f"❌ {proxy[:30]}... - Formato inválido")
                continue
            
            proxy_dict = ProxyParser.to_requests_dict(proxy_info)
            
            start = time.time()
            response = requests.get(
                'http://httpbin.org/ip',
                proxies=proxy_dict,
                timeout=5,
                verify=False
            )
            elapsed = time.time() - start
            
            if response.status_code == 200:
                vivos += 1
                detalles.append(f"✅ {proxy[:30]}... - {elapsed:.2f}s")
            else:
                muertos += 1
                detalles.append(f"❌ {proxy[:30]}... - HTTP {response.status_code}")
                
        except Exception as e:
            muertos += 1
            detalles.append(f"❌ {proxy[:30]}... - Error")
    
    texto = f"""✅ *TEST RÁPIDO COMPLETADO*
━━━━━━━━━━━━━━━━━━━━━━
📊 *RESULTADOS:*

✅ Vivos: {vivos}
❌ Muertos: {muertos}
━━━━━━━━━━━━━━━━━━━━━━

📁 Detalles enviados en archivo"""
    
    try:
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode='Markdown')
    except:
        bot.send_message(msg.chat.id, texto, parse_mode='Markdown')
    
    filename = f"px_rapido_{datetime.now().strftime('%Y%m%d%H%M%S')}.txt"
    with open(filename, 'w', encoding='utf-8') as f:
        f.write("RESULTADOS TEST RÁPIDO\n")
        f.write("━━━━━━━━━━━━━━━━━━━━━━\n\n")
        for d in detalles:
            f.write(f"{d}\n")
    
    with open(filename, 'rb') as f:
        bot.send_document(msg.chat.id, f, caption="📊 Detalles del test rápido")
    
    os.remove(filename)

# ==================== COMANDOS DE SITIOS SHOPIFY ====================

@bot.message_handler(commands=['addsh'])
@requiere_acceso
def cmd_add_sitio(message):
    try:
        url = message.text.split()[1]
        if guardar_sitio(url):
            bot.reply_to(message, f"✅ Sitio guardado:\n{url}")
        else:
            bot.reply_to(message, "❌ Error: El sitio ya existe o URL inválida")
    except IndexError:
        bot.reply_to(message, "❌ Uso: /addsh https://tienda.myshopify.com")

@bot.message_handler(commands=['sitios'])
@requiere_acceso
def cmd_listar_sitios(message):
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

@bot.message_handler(commands=['delsh'])
@requiere_acceso
def cmd_del_sitio(message):
    try:
        url = message.text.split()[1]
        if eliminar_sitio(url):
            bot.reply_to(message, f"✅ Sitio eliminado: {url[:30]}...")
        else:
            bot.reply_to(message, "❌ Sitio no encontrado")
    except IndexError:
        bot.reply_to(message, "❌ Uso: /delsh https://tienda.myshopify.com")

# ==================== COMANDOS DE TARJETAS ====================

@bot.message_handler(commands=['check'])
@requiere_acceso
def cmd_check(message):
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
            bot.edit_message_text(texto_premium, msg.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", msg.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /check NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['pp'])
@requiere_acceso
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
            bot.edit_message_text(texto_premium, msg.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", msg.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['pp2'])
@requiere_acceso
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
            bot.edit_message_text(texto_premium, msg.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", msg.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp2 NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['pp3'])
@requiere_acceso
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
            bot.edit_message_text(texto_premium, msg.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", msg.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /pp3 NUMERO|MES|AÑO|CVV")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['sh'])
@requiere_acceso
def cmd_shopify(message):
    try:
        partes = message.text.split()
        cc = partes[1]
        
        if len(cc.split('|')) != 4:
            bot.reply_to(message, "❌ Formato incorrecto. Usa: NUMERO|MES|AÑO|CVV")
            return
        
        sitios = obtener_sitios()
        
        if not sitios:
            bot.reply_to(message, "❌ No hay sitios guardados. Usa /addsh para agregar uno.")
            return
        
        if len(partes) == 3:
            url = partes[2]
            if url not in sitios:
                bot.reply_to(message, "❌ Sitio no encontrado en tu lista")
                return
        else:
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
            
            actualizar_estadisticas_sitio(url, mejor_resultado['success'])
            
            texto_premium = formato_check_premium(cc, mejor_resultado, bin_info, mejor_resultado['tiempo'], user_name, "Shopify")
            bot.edit_message_text(texto_premium, msg.chat.id, msg.message_id)
        else:
            bot.edit_message_text("❌ No se pudo verificar", msg.chat.id, msg.message_id)
        
    except IndexError:
        bot.reply_to(message, "❌ Uso: /sh NUMERO|MES|AÑO|CVV [URL]")
    except Exception as e:
        bot.reply_to(message, f"❌ Error: {str(e)}")

@bot.message_handler(commands=['bin'])
@requiere_acceso
def cmd_bin(message):
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
        
        bot.edit_message_text(texto, msg.chat.id, msg.message_id, parse_mode='Markdown')
        
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
    texto = (
        "╔════════════════════════════╗\n"
        "║    🚀  AUTO SHOPIFY BOT    ║\n"
        "╠════════════════════════════╣\n"
        "║  📌 PROXIES                  ║\n"
        "║  • /addproxy - Añadir       ║\n"
        "║  • /testproxy - Test avanzado║\n"
        "║  • /testone - Test individual║\n"
        "║  • /px - Test rápido        ║\n"
        "║  • /proxies - Listar        ║\n"
        "║  • /formatos - Ver formatos ║\n"
        "║  • /delallproxy - Eliminar  ║\n"
        "║                            ║\n"
        "║  💳 TARJETAS                ║\n"
        "║  • /check CC - Stripe $1   ║\n"
        "║  • /pp CC - PayPal $10     ║\n"
        "║  • /pp2 CC - PayPal $0.10  ║\n"
        "║  • /pp3 CC - PayPal $1     ║\n"
        "║  • /sh CC - AutoShopify    ║\n"
        "║  • /mass - Stripe masivo   ║\n"
        "║  • /mpp - PayPal masivo    ║\n"
        "║  • /msh - Shopify masivo   ║\n"
        "║                            ║\n"
        "║  🛍️ SITIOS                 ║\n"
        "║  • /addsh - Añadir sitio   ║\n"
        "║  • /sitios - Listar        ║\n"
        "║  • /delsh - Eliminar sitio ║\n"
        "║                            ║\n"
        "║  📊 OTROS                  ║\n"
        "║  • /bin BIN - Consultar    ║\n"
        "║  • /stats - Estadísticas   ║\n"
        "╚════════════════════════════╝"
    )
    bot.send_message(message.chat.id, texto)

@bot.message_handler(commands=['help'])
@requiere_acceso
def cmd_help(message):
    texto = (
        "╔════════════════════════════╗\n"
        "║        ❓ AYUDA             ║\n"
        "╠════════════════════════════╣\n"
        "║  • Usa /menu para ver      ║\n"
        "║    todas las opciones      ║\n"
        "║                            ║\n"
        "║  • Para proxies:            ║\n"
        "║    /testone - Test detallado║\n"
        "║    /testproxy - Test masivo ║\n"
        "║    /formatos - Ver formatos ║\n"
        "║                            ║\n"
        "║  • Formatos soportados:     ║\n"
        "║    ip:puerto                ║\n"
        "║    ip:puerto:user:pass      ║\n"
        "║    user:pass@ip:puerto      ║\n"
        "║    protocolo://ip:puerto    ║\n"
        "╚════════════════════════════╝"
    )
    bot.reply_to(message, texto)

@bot.message_handler(commands=['stats'])
@requiere_acceso
def cmd_stats(message):
    stats = obtener_estadisticas()
    
    texto = f"""📊 *ESTADÍSTICAS GLOBALES*

🌐 *PROXIES*
• Guardados: {stats['proxies']}
• Éxitos: {stats['exits_proxy']}
• Fallos: {stats['fallos_proxy']}

💳 *TARJETAS*
• Totales: {stats['tarjetas']}

📝 *VERIFICACIONES*
• Totales: {stats['checks']}
• Aprobadas: {stats['aprobadas']}
• Tasa: {(stats['aprobadas']/stats['checks']*100) if stats['checks']>0 else 0:.1f}%"""
    
    bot.reply_to(message, texto, parse_mode='Markdown')

# ==================== VERIFICACIÓN MASIVA (resumen) ====================
# Por razones de espacio, se incluyen solo las funciones principales
# Las funciones masivas (/mass, /mpp, /msh) mantienen su código original

# ==================== CALLBACKS ====================

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
            elif re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+', linea) or \
                 re.match(r'^https?://', linea) or \
                 re.search(r'@', linea):
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
            guardados, repetidos, invalidos, errores = guardar_proxies_desde_texto(contenido)
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
        
        bot.edit_message_text(texto, msg.chat.id, msg.message_id)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Error: {str(e)}", msg.chat.id, msg.message_id)

# ==================== MANEJADOR POR DEFECTO ====================

@bot.message_handler(func=lambda m: True)
def default_handler(message):
    if message.text and message.text.startswith('/'):
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
    print("✅ Características principales:")
    print("   • Sistema de keys de acceso")
    print("   • Soporte para TODOS los formatos de proxy")
    print("   • Test de proxy avanzado con múltiples endpoints")
    print("   • Verificaciones Stripe, PayPal y AutoShopify")
    print("   • Gestión de tarjetas y sitios")
    print("="*80)
    print("📱 Usa /start para comenzar")
    print("="*80)
    
    try:
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        print(f"❌ Error en polling: {e}")
        time.sleep(10)
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
