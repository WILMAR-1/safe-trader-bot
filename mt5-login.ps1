# =============================================================
#  Auto-login de MetaTrader 5 (Exness) sin interfaz grafica.
#
#  Lee las credenciales de tu .env, genera el archivo de
#  configuracion que MT5 admite para iniciar sesion solo, y
#  reinicia la terminal dentro del contenedor.
#
#  USO:   .\mt5-login.ps1
#
#  Tu contrasena NO se muestra por pantalla ni se guarda fuera
#  del contenedor.
# =============================================================

$ErrorActionPreference = "Stop"
$docker = "C:\Program Files\Docker\Docker\resources\bin\docker.exe"
if (-not (Test-Path $docker)) { $docker = "docker" }

# --- 1. Leer credenciales del .env ---
if (-not (Test-Path ".env")) { throw "No encuentro el archivo .env en esta carpeta." }
$cfg = @{}
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') { $cfg[$matches[1]] = $matches[2].Trim() }
}
$login = $cfg['EXNESS_LOGIN']
$server = $cfg['EXNESS_SERVER']
$password = $cfg['EXNESS_PASSWORD']

if (-not $login -or -not $server -or -not $password) {
    throw "Faltan EXNESS_LOGIN / EXNESS_SERVER / EXNESS_PASSWORD en el .env"
}
Write-Host "Cuenta : $login"
Write-Host "Servidor: $server"
Write-Host "Password: [leida del .env, $($password.Length) caracteres]"

# --- 2. Generar el INI de auto-login ---
$lines = @(
    "[Common]",
    "Login=$login",
    "Password=$password",
    "Server=$server",
    "AutoConfiguration=false",
    "EnableNews=false",
    "EnableDDE=false",
    "CertInstall=true",
    "",
    "[Experts]",
    "AllowLiveTrading=true",
    "AllowDllImport=false",
    "Enabled=true",
    "Account=false",
    "Profile=false"
)
$tmp = Join-Path $env:TEMP "mt5_autologin.ini"
[System.IO.File]::WriteAllLines($tmp, $lines)

# --- 3. Copiar al contenedor y dar permisos ---
Write-Host "`nCopiando configuracion al contenedor..."
& $docker cp $tmp safe-trader-mt5:/config/autologin.ini
& $docker exec -u root safe-trader-mt5 chown abc:abc /config/autologin.ini
[System.IO.File]::Delete($tmp)

# --- 4. Reiniciar la terminal MT5 con auto-login ---
Write-Host "Reiniciando MetaTrader 5 con auto-login..."
& $docker exec -u abc safe-trader-mt5 sh -c "pkill -f terminal64.exe; sleep 3"
& $docker exec -u abc -d safe-trader-mt5 sh -c "export WINEPREFIX=/config/.wine WINEDEBUG=-all HOME=/config DISPLAY=:1; wine '/config/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe' /config:'C:\autologin.ini'"

Write-Host "`nEsperando a que MT5 inicie sesion (60s)..."
Start-Sleep -Seconds 60

# --- 5. Comprobar ---
Write-Host "`n=== Comprobando conexion ==="
& $docker compose logs --since 3m safe-trader-bot 2>&1 | Select-String "CONECTADO|Esperando a MT5" | Select-Object -Last 3
Write-Host "`nSi sigue diciendo 'Esperando a MT5', dale 1-2 minutos mas y vuelve a mirar con:"
Write-Host "  docker compose logs --tail=20 safe-trader-bot"
