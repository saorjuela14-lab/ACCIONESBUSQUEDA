# Pinger externo (keep-alive de producción)

Producción corre en FastAPI Cloud con 0-1 réplicas: si nadie le hace requests, se duerme y el Autopilot no corre ciclos. El 21-oct-2026 el pinger del box falló y hubo más de 2 h sin ciclos en sesión.

## Principal: GitHub Actions
`.github/workflows/keepalive-pinger.yml` hace un GET a `/health` cada 5 min, L-V, de 08:20 a 15:05 COT.
- No usa secretos (el endpoint es público) y tiene permisos `contents: read`.
- Consumo: ~82 ejecuciones/día × ~22 días ≈ 1.800 ejecuciones/mes, cada una cobrada como 1 minuto. Como el repo es público, los minutos de Actions son gratis e ilimitados. En un repo privado, eso cabe en los 2.000 min/mes del plan Free solo con poco margen.
- Limitación: GitHub puede retrasar o saltarse ejecuciones programadas cuando hay carga, y desactiva los crons tras 60 días sin actividad en el repo.

## Respaldo recomendado: monitor de uptime gratuito
Se crea a mano, fuera del repo:
1. Crear una cuenta gratis en UptimeRobot (o similar).
2. Crear un monitor HTTP(s) hacia `https://accionesbusqueda.fastapicloud.dev/health`, con intervalo de 5 min.
3. Opcional: activar una alerta por email si cae.
El plan gratuito pinga 24/7, lo que también mantiene la app despierta fuera de sesión. Eso no tiene costo.
