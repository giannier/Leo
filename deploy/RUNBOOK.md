# Runbook de producción — Leo (TASK-026)

Cómo poner Leo en un VPS (Contabo) y operarlo. Va **después** de la prueba con el número real
(TASK-459, `leo-sdd/features/004-motor-de-ventas/milestones/numero-real.md`).

Lo que hay en `deploy/`:

| Archivo | Para qué |
|---|---|
| `docker-compose.prod.yml` | Caddy (HTTPS), backend, worker, frontend, Postgres, Redis, MinIO, migraciones |
| `Caddyfile` | `/api` y `/webhooks` → backend; el resto → frontend; `files.` → MinIO |
| `env.prod.example` | Plantilla de `deploy/.env.prod` (secretos; nunca se sube al repo) |
| `backup.sh` / `restore.sh` | Copia diaria cifrada de la base y los archivos, y su restauración |
| `.github/workflows/deploy.yml` | Despliegue manual desde GitHub (botón "Run workflow") |

---

## 1. Antes de empezar

- **VPS:** Ubuntu 24.04, 4 vCPU, 8 GB de RAM y 100 GB de disco alcanzan para el piloto.
- **Dominio:** tres registros DNS tipo A apuntando a la IP del VPS:
  `app.tudominio.pe`, `files.app.tudominio.pe` y `status.app.tudominio.pe` (este último solo si usas
  el monitor). Caddy pide los certificados solo cuando los nombres ya resuelven.
- **Cuentas:** OpenAI (clave de API, con un límite mensual de gasto), Resend (dominio de correo
  verificado) y YCloud (el número conectado en TASK-459).

## 2. Preparar el VPS (una vez)

```bash
# Como root
adduser leo && usermod -aG docker leo         # usuario sin privilegios para operar
apt update && apt install -y docker.io docker-compose-v2 git ufw
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable

# Como leo
sudo mkdir -p /opt/leo /var/backups/leo && sudo chown leo: /opt/leo /var/backups/leo
git clone https://github.com/giannier/leo.git /opt/leo
cd /opt/leo && git checkout main
cp deploy/env.prod.example deploy/.env.prod && chmod 600 deploy/.env.prod
nano deploy/.env.prod     # llena TODO: dominio, secretos, claves
```

Genera los secretos **en el VPS**, no los copies del entorno de desarrollo:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"            # SECRET_KEY
python3 -c "import secrets; print(secrets.token_urlsafe(24))"            # contraseñas de Postgres y MinIO, BACKUP_PASSPHRASE
docker run --rm python:3.12-slim sh -c "pip -q install cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"   # CREDENTIALS_ENCRYPTION_KEY
```

Guarda **fuera del VPS** (gestor de contraseñas) `CREDENTIALS_ENCRYPTION_KEY` y `BACKUP_PASSPHRASE`.
Sin la primera, las credenciales de WhatsApp guardadas no se pueden leer; sin la segunda, ningún backup
se puede restaurar.

## 3. Primer despliegue

```bash
cd /opt/leo
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod up -d --build
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod ps
curl -s https://app.tudominio.pe/api/v1/health      # {"status":"ok",...}
```

El orden lo resuelve el compose: Postgres, Redis y MinIO arrancan; se crea el bucket; corren las
migraciones; recién entonces arrancan el backend y el worker.

Hazte administrador de la plataforma (después de registrarte en `https://app.tudominio.pe/register`):

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod \
  exec backend python -m scripts.grant_platform_admin tu@correo.com
```

### Prueba de humo (Verification de TASK-026)

1. Registrar un negocio nuevo, verificar el correo (llega por Resend) y el teléfono.
2. Completar la configuración inicial y cargar un producto con foto.
3. Abrir el Modo prueba, escribir "¿Cuánto cuesta?" y ver la respuesta del asistente.
4. Panel de operación (`/admin`): el negocio aparece en Negocios.

## 4. Conectar WhatsApp (YCloud)

- **URL del webhook en YCloud:** `https://app.tudominio.pe/webhooks/ycloud`
- `YCLOUD_WEBHOOK_SECRET` en `deploy/.env.prod` = el secreto que muestra YCloud; luego
  `docker compose ... up -d backend worker`.
- El negocio pide la conexión desde **Mi número**; tú la conectas en **/admin → Solicitudes** con la API
  key, el WABA ID y el phone number ID de YCloud (igual que en TASK-459).

## 5. Actualizar

**Desde GitHub** (recomendado): Actions → **Deploy** → *Run workflow* → rama o tag. Necesita los
secretos `DEPLOY_HOST`, `DEPLOY_USER` (= `leo`), `DEPLOY_SSH_KEY` y `DEPLOY_PATH` (= `/opt/leo`).
Construye, migra, reinicia y espera a que el backend responda; si no responde, muestra los logs y falla.

**A mano**, en el VPS:

```bash
cd /opt/leo && git fetch && git checkout --detach origin/main
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod up -d --build
```

Antes de una actualización con migraciones, corre un backup (sección 6).

## 6. Backups

Diario, cifrado, 14 días de retención, con cron del usuario `leo`:

```bash
crontab -e
15 4 * * * cd /opt/leo && deploy/backup.sh >> /var/log/leo-backup.log 2>&1
```

Escribe en `/var/backups/leo` dos archivos por día: `leo-db-*.dump.enc` (la base) y
`leo-files-*.tar.gz.enc` (fotos, documentos, comprobantes). **Cópialos fuera del VPS** al menos una vez
por semana (por ejemplo, `rsync` a otra máquina o el almacenamiento de objetos del proveedor): un
backup en el mismo disco no sobrevive a perder el disco.

### Restaurar (y probarlo una vez: es parte de la Verification)

```bash
cd /opt/leo
deploy/restore.sh /var/backups/leo/leo-db-AAAAMMDDTHHMMSSZ.dump.enc \
                  /var/backups/leo/leo-files-AAAAMMDDTHHMMSSZ.tar.gz.enc
```

Detiene el backend y el worker, reemplaza la base (y los archivos si los das), y los arranca. Pide
escribir `restaurar` antes de tocar nada, y si la clave del backup no coincide se detiene sin cambiar
nada. Para el simulacro, hazlo una vez recién desplegado, con datos de prueba.

## 7. Operación diaria

| Qué | Cómo |
|---|---|
| Ver logs | `docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod logs -f backend worker` |
| Extender la prueba de un negocio | `/admin → Negocios →` el negocio `→ Extender la prueba` (pide un motivo; queda auditado) |
| Webhooks que no se pudieron procesar | `/admin → Sin procesar` |
| Envíos fallidos y números en riesgo | `/admin/metricas` (Operación) |
| Precio de los modelos (costos y márgenes) | `/admin/metricas → Precio de los modelos` |
| Reiniciar un servicio | `docker compose ... restart worker` |
| Espacio en disco | `df -h` y `docker system df`; `docker image prune -f` limpia imágenes viejas |

### Monitor (opcional)

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env.prod --profile monitoring up -d
```

Abre `https://status.app.tudominio.pe`, crea el usuario y agrega un monitor HTTP a
`https://app.tudominio.pe/api/v1/health` cada 60 s, con aviso por correo o Telegram.

**Sentry** (errores con traza) no está incluido: necesita una dependencia nueva (`sentry-sdk`) y está
pendiente de tu aprobación.

## 8. Cobro de la plataforma

Sale **apagado** (`BILLING_ENFORCED=false`): la prueba no vence, el cupo se cuenta pero no se aplica y
el panel muestra "Modo pruebas". Se enciende con `BILLING_ENFORCED=true` en `deploy/.env.prod` y
`docker compose ... up -d backend worker` **solo cuando Leo empiece a vender, con la confirmación de
Giannier**. `BILLING_PAYMENT_INSTRUCTIONS` es el texto que ve el negocio para pagar su plan.

## 9. Si algo sale mal

- **El backend no arranca y el log dice SECRET_KEY / CREDENTIALS_ENCRYPTION_KEY / TRUST_PROXY_HEADERS:**
  falta ese valor en `.env.prod` (Leo se niega a arrancar en producción con claves de desarrollo).
- **"No pudimos guardar la foto":** MinIO o el bucket; mira `docker compose ... logs minio minio-init`.
- **Los mensajes de WhatsApp no llegan:** la URL y el secreto del webhook en YCloud (sección 4), y
  `logs backend | grep webhook`.
- **Certificado HTTPS que no sale:** los registros DNS aún no apuntan al VPS; `logs caddy`.
- **Volver a la versión anterior:** `git checkout --detach <commit anterior>` y `up -d --build`. Si la
  versión nueva migró la base, restaura el backup previo (sección 6).
