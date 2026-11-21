param([Parameter(Mandatory = $true)][System.Net.IPAddress]$Address)

$ErrorActionPreference = 'Stop'
if ($Address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
    throw 'Specify the IPv4 address of the computer on the phone Wi-Fi network.'
}
if (!(Get-NetIPAddress -IPAddress $Address.IPAddressToString -ErrorAction SilentlyContinue)) {
    throw 'The address must belong to this computer.'
}
$web = Join-Path (Split-Path $PSScriptRoot -Parent) 'fitness-agent-web'
$tlsDir = Join-Path $web '.live-tls'
New-Item -ItemType Directory -Force $tlsDir | Out-Null
$openSsl = (Get-Command openssl).Source

function Invoke-TlsCommand {
    & $openSsl @args
    if ($LASTEXITCODE -ne 0) { throw 'OpenSSL could not prepare the test certificate.' }
}

$ip = $Address.IPAddressToString
$ca = Join-Path $tlsDir 'ICT2-Phone-Test-CA.pem'
$phoneCa = Join-Path $tlsDir 'ICT2-Phone-Test-CA.cer'
$caKey = Join-Path $tlsDir 'ca.key'
$cert = Join-Path $tlsDir 'server.pem'
$key = Join-Path $tlsDir 'server.key'
$csr = Join-Path $tlsDir 'server.csr'
$ext = Join-Path $tlsDir 'server-ext.cnf'
$conf = Join-Path $tlsDir 'request.cnf'
@('[req]', 'distinguished_name=dn', '[dn]') | Set-Content $conf -Encoding ascii
$caCreated = !(Test-Path $ca) -or !(Test-Path $caKey)
if (!$caCreated) {
    & $openSsl x509 -in $ca -noout -checkend 86400 | Out-Null
    $caCreated = $LASTEXITCODE -ne 0
}
if ($caCreated) {
    Invoke-TlsCommand req -config $conf -x509 -newkey rsa:2048 -sha256 -nodes -days 14 -subj '/CN=ICT2 Phone Test CA' -addext 'basicConstraints=critical,CA:TRUE,pathlen:0' -addext 'keyUsage=critical,keyCertSign,cRLSign' -keyout $caKey -out $ca
}
$previousCert = if ((Test-Path $cert) -and (Test-Path $key)) { (& $openSsl x509 -in $cert -noout -checkip $ip -checkend 86400) -join "`n" }
if ($caCreated -or ($previousCert -notmatch 'does match') -or ($previousCert -notmatch 'will not expire')) {
    @("subjectAltName=IP:$ip", 'basicConstraints=critical,CA:FALSE', 'keyUsage=critical,digitalSignature,keyEncipherment', 'extendedKeyUsage=serverAuth') | Set-Content $ext -Encoding ascii
    Invoke-TlsCommand req -config $conf -new -newkey rsa:2048 -nodes -subj "/CN=$ip" -keyout $key -out $csr
    Invoke-TlsCommand x509 -req -in $csr -CA $ca -CAkey $caKey -CAcreateserial -out $cert -days 7 -sha256 -extfile $ext
}
Invoke-TlsCommand x509 -in $ca -outform DER -out $phoneCa
$env:LIVE_TLS_CERT = $cert
$env:LIVE_TLS_KEY = $key
Push-Location $web
try {
    npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    if (!(Test-Path 'dist/live-assets/pose_landmarker_lite.task')) { throw 'The workspace pose model is missing.' }
    Write-Host "Phone test page: https://$($ip):5443/live"
    Write-Host "Trust this public test CA on the phone: $phoneCa"
    npm.cmd run preview -- --host $ip --port 5443 --strictPort
    if ($LASTEXITCODE -ne 0) { throw 'The HTTPS preview could not start.' }
} finally {
    Pop-Location
}
