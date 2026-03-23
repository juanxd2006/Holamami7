#!/bin/bash
# Script de instalación para Termux

echo "📦 Instalando dependencias..."
pkg update && pkg upgrade -y
pkg install python clang python-pip -y

echo "🐍 Instalando paquetes Python..."
pip install pytelegrambotapi requests

echo "✅ Instalación completada!"
echo "🚀 Para ejecutar el bot: python bot.py"
echo ""
echo "📝 No olvides editar el archivo bot.py y poner tu TOKEN"
