# 🛡️ ORBIT ALLIANCE ADM - Kʆɛɓɛʀ

> O primeiro ADM do Telegram com cérebro de IA que lê BIO + entende FOTO/GIF/STICKER pornô.

**Assinatura Oficial:** `Kʆɛɓɛʀ`  
**Dono Supremo:** `8398287578`  
**Status:** 100% ON - 1ª Base IA Visão + 2ª Base Fallback

---

### ⚡ O QUE ELE FAZ?

Diferente de bot de comando, ele é um **ADM raiz com IA**.

1.  **LÊ A BIO/DESCRIÇÃO:** Se na bio tá "Proibido link, +18, política" e o botão Seguir Bio ON, ele protege pela bio.
2.  **9 BOTÕES FIXOS:** Bio, Anti-Link, Anti +18, Anti-Briga, Anti-Flert, Anti-Política, Anti-Flood, Anti-Venda, Boas-vindas.
3.  **DETECTA MÍDIA PORNÔ:** Foto nua, vídeo, gif, sticker hentai sem legenda? **Gemini Vision** vê e apaga.
4.  **SE A IA FALHAR - 2ª BASE:** Vira ADM tradicional e bloqueia toda mídia sem legenda se Anti +18 ON.
5.  **NUNCA MODERA ADM:** ADM humano e BOT são ignorados. `CREATOR_ID` nunca é moderado.

### Fluxo
`MSG → É CREATOR_ID/ADM? IGNORO → Carrego Bio+Botões → IA Visão/Texto → Violação? → Regra Ativa? → Tenho permissão? → APAGO`

---

### 🚀 DEPLOY NO RENDER (100% Grátis)

1.  Faça fork desse repo
2.  No Render > New Web Service > Conecte o GitHub
3.  **Build Command:** `pip install -r requirements.txt`
4.  **Start Command:** `gunicorn main:app`
5.  **Environment Variables:**

| Variável | Exemplo | Obrigatória |
| :--- | :--- | :--- |
| `BOT_TOKEN` | `123456:ABC...` | SIM |
| `BOT_ID` | `123456789` | SIM |
| `CREATOR_ID` | `8398287578` | SIM |
| `WEBHOOK_SECRET` | `orbit_2025_secure_123` | SIM |
| `GEMINI_API_KEY` | `AIza...` | SIM - Pra visão |
| `GROQ_API_KEY` | `gsk_...` | SIM |
| `CEREBRAS_API_KEY` | `csk_...` | Opcional |
| `MISTRAL_API_KEY` | `...` | Opcional |

6.  Após deploy, ative o webhook (1 vez no navegador):
