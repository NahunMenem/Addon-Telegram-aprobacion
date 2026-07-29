# Aprobaciones de compras: Jira Cloud + Telegram

Backend FastAPI que recibe solicitudes del proyecto Jira Service Management `SEAL`,
notifica a un administrador por Telegram y registra decisiones de aprobación o
rechazo mediante transiciones de Jira.

No configura webhooks automáticamente ni ejecuta acciones durante la instalación.
Todos los secretos se leen desde variables de entorno.

## Arquitectura y seguridad

- `POST /webhooks/jira`: valida `X-Webhook-Secret`, exige el estado
  `Aviso por Telegram`, extrae los campos configurados y envía el mensaje.
- `POST /webhooks/telegram`: valida
  `X-Telegram-Bot-Api-Secret-Token`, el user ID administrador, el token firmado,
  su vencimiento y su vínculo con el issue.
- Los callbacks tienen como máximo 64 bytes. Los tokens HMAC son compactos,
  opacos, expiran y se almacenan vinculados al issue.
- Una actualización atómica en base de datos reclama la decisión antes de llamar
  a Jira. El issue y los tokens son únicos, lo que evita reenvíos y dobles clics.
- Antes de transicionar, se vuelve a consultar el estado actual de Jira.
- Los clientes usan `httpx.AsyncClient`, timeout y validación de respuestas.
- Los logs sólo incluyen la clave del issue en los errores; nunca tokens,
  credenciales, payloads ni detalles de compra.

La persistencia usa SQLAlchemy asíncrono. SQLite funciona de forma inmediata y
una URL `postgresql://...` selecciona automáticamente el driver `asyncpg`.

## Estructura

```text
app/
  config.py       variables y validaciones
  database.py     operaciones atómicas
  jira.py         Jira REST API v3 y extracción del webhook
  main.py         endpoints y orquestación
  models.py       tablas SQLAlchemy
  schemas.py      modelos de entrada
  security.py     HMAC, expiración y comparación segura
  telegram.py     Telegram Bot API
tests/            pruebas unitarias y de endpoints
main.py           entrada de Uvicorn
```

## Ejecución local en Windows

Requiere Python 3.10 o posterior. En PowerShell, desde esta carpeta:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Complete `.env` como se explica abajo y ejecute:

```powershell
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Compruebe `http://127.0.0.1:8000/health`. Para que Jira y Telegram lleguen al
equipo local hace falta una URL HTTPS pública (por ejemplo, un túnel). Esa URL
debe ser `APP_BASE_URL`; no se crea ningún túnel desde esta aplicación.

Para ejecutar las pruebas, que usan SQLite temporal y clientes simulados:

```powershell
pytest -q
```

## Variables

Copie `.env.example` a `.env`. No confirme `.env` en Git.

| Variable | Qué completar |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token entregado por BotFather. |
| `TELEGRAM_ADMIN_CHAT_ID` | Chat donde el bot enviará solicitudes. Puede ser negativo en grupos. |
| `TELEGRAM_ADMIN_USER_ID` | ID numérico personal del único usuario que puede decidir. No es el chat ID. |
| `TELEGRAM_WEBHOOK_SECRET` | Secreto aleatorio de al menos 16 caracteres, admitido por `setWebhook`. Use sólo caracteres permitidos por Telegram: `A-Z`, `a-z`, `0-9`, `_` y `-`. |
| `JIRA_BASE_URL` | `https://docya.atlassian.net`. |
| `JIRA_EMAIL` | Email de la cuenta técnica o usuario de Jira. |
| `JIRA_API_TOKEN` | API token de Atlassian con acceso al proyecto y permisos para comentar/transicionar. |
| `JIRA_WEBHOOK_SECRET` | Secreto aleatorio de al menos 16 caracteres que también enviará el webhook. |
| `JIRA_PRODUCT_FIELD_ID` | ID tipo `customfield_12345`. |
| `JIRA_PURCHASE_URL_FIELD_ID` | ID del campo URL de compra. |
| `JIRA_AMOUNT_FIELD_ID` | ID del campo importe. |
| `APP_BASE_URL` | URL HTTPS pública, sin barra final. |
| `APP_SIGNING_SECRET` | Secreto aleatorio de al menos 32 caracteres, distinto de los demás. |
| `DATABASE_URL` | Local: `sqlite:///./approvals.db`. Railway/PostgreSQL: la URL entregada por el servicio. |

Los nombres de estados y los timeouts pueden ajustarse con las variables
opcionales incluidas en `.env.example`.

Para generar secretos en PowerShell sin almacenarlos en el historial:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

## Identificar los campos personalizados

No adivine los IDs. Con una cuenta autorizada puede inspeccionar los campos desde
la administración de Jira o consultar `GET /rest/api/3/field` y localizar los
campos por nombre. Configure el ID completo (`customfield_...`) en las tres
variables. El solicitante se toma de `reporter` y, si falta, de `creator`.

El webhook debe incluir al menos `issue.key` e `issue.fields`, incluyendo:
`summary`, `status`, `reporter` (o `creator`) y los tres campos personalizados.

## Configurar el webhook de Jira

Cuando la aplicación ya tenga URL HTTPS:

1. En Jira Automation para el proyecto `SEAL`, cree una regla que se active al
   pasar al estado `Aviso por Telegram`.
2. Agregue la acción **Send web request**.
3. URL: `https://SU-DOMINIO/webhooks/jira`.
4. Método: `POST`; header `Content-Type: application/json`.
5. Agregue `X-Webhook-Secret` con exactamente el valor de
   `JIRA_WEBHOOK_SECRET`.
6. Envíe el issue completo con sus campos. Una forma típica es usar el cuerpo
   del webhook de Jira que contiene `{"issue": {{issue.toJson}}}`; valide la
   sintaxis disponible en su edición de Automation.
7. Pruebe primero con un issue de prueba. Un estado distinto devuelve HTTP 409,
   un secreto incorrecto devuelve 401 y un evento válido devuelve 202.

La validación del estado se hace tanto al recibir la notificación como justo
antes de decidir.

## Configurar el webhook de Telegram

Después del despliegue, haga una única llamada manual a la API oficial:

```text
POST https://api.telegram.org/bot<BOT_TOKEN>/setWebhook
```

Cuerpo JSON:

```json
{
  "url": "https://SU-DOMINIO/webhooks/telegram",
  "secret_token": "EL_MISMO_VALOR_DE_TELEGRAM_WEBHOOK_SECRET",
  "allowed_updates": ["callback_query"],
  "drop_pending_updates": true
}
```

Telegram enviará el secreto en
`X-Telegram-Bot-Api-Secret-Token`. No coloque el token del bot en
`APP_BASE_URL`, logs ni archivos versionados.

## Despliegue en Railway

1. Cree un proyecto desde este repositorio. Railway detectará el `Dockerfile`.
2. Agregue un volumen persistente si conservará SQLite y monte una ruta, por
   ejemplo `/data`; use `DATABASE_URL=sqlite:////data/approvals.db`.
   Para producción se recomienda agregar PostgreSQL y usar la variable de URL
   provista por Railway.
3. Cargue todas las variables de `.env.example` en **Variables**, sin subir el
   archivo `.env`.
4. Genere un dominio HTTPS de Railway y úselo como `APP_BASE_URL`.
5. Despliegue y confirme que `https://DOMINIO/health` responde
   `{"status":"ok"}`.
6. Recién entonces configure ambos webhooks con ese dominio.

La aplicación escucha el puerto indicado por `PORT`. En despliegues con varias
réplicas use PostgreSQL; un archivo SQLite no debe compartirse entre réplicas.

## Notas operativas

- La cuenta de Jira necesita permisos para ver issues, ejecutar las transiciones
  y agregar comentarios.
- Las transiciones se buscan por el nombre del **estado de destino**, no por un
  ID fijo.
- Si Jira rechaza una acción, el mensaje no se marca como aprobado/rechazado.
- Si Jira fue actualizado pero Telegram no pudo editar el mensaje, Jira y la
  auditoría en base quedan como fuente de verdad y el fallo se registra sin
  secretos.
- Las tablas se crean al arrancar. Para cambios de esquema futuros conviene
  incorporar Alembic antes de migrar una base con datos reales.
