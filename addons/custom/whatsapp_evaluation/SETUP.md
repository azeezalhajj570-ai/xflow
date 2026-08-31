# Evolution API Setup Guide (v2)

This module communicates with the [Evolution API](https://github.com/EvolutionAPI/evolution-api). You must have a running instance of Evolution API to use this connector.

## Option 1: Self-Hosted (Docker)
You can run Evolution API on any VPS (Linux, Ubuntu) with Docker installed.

### 1. Install Docker
```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
```

### 2. Run Evolution API
Run the following command to start the API:

```bash
docker run -d --name evolution-api \
  --restart always \
  -p 8080:8080 \
  -e AUTHENTICATION_API_KEY=YOUR_GLOBAL_KEY \
  -e SERVER_URL=https://your-domain.com \
  atendai/evolution-api:v2.1.0
```
*(Replace `YOUR_GLOBAL_KEY` with a strong secret string)*

### 3. Connect Odoo
1. Go to **WhatsApp Evaluation Accounts**.
2. Create new Account.
3. **Base URL**: `http://your-ip:8080` (or your domain).
4. **Global API Key**: The key you set in `AUTHENTICATION_API_KEY`.
5. **Instance Name**: Create a name (e.g., `odoo-main`).
6. Click **Test Credentials**.

---

## Option 2: Managed Setup
Don't want to manage servers? We offer a **White-Glove Setup Service**.
We will:
- Provision a secure VPS.
- Install Evolution API (v2).
- Configure SSL/HTTPS (LetsEncrypt).
- Connect it to your Odoo instance.

**[Contact Us for Setup](mailto:support@example.com?subject=Evolution_Setup_Request)**
