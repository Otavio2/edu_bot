# 🚀 Orbit Alliance V16 - HANSEL + IA ADM MAX + PV LIVRE + LEARNING BEHAVIOR

> ----- Criador: Kʆɛɓɛʀ -----

Bot de moderação autônoma para Telegram com IA real, sistema de aprendizado por grupo e infraestrutura anti-queda.

## ✨ Novidades V16 - LEARNING

A IA agora aprende com o próprio grupo:

- **Horário de Pico:** Conta msgs por hora (`group_peak`). Se detectar pico > 1.5x média, fica mais rígido (flood_limit cai de 7 para 3).
- **Spammer Recorrente:** Usuário que levou 3+ deletes/mutes entra em observação (`user_reputation`). Na próxima msg já entra mutado automático.
- **Palavras Tóxicas do Grupo:** Detecta briga (2+ tóxicos em 30s), extrai palavras em comum e salva em `group_toxic_words`. Da próxima vez apaga antes mesmo da IA geral pegar.

## 🤖 IA

- **Providers:** Groq, Gemini, Cerebras com fallback automático
- **Blacklist inteligente:** Modelo/provedor com erro é bloqueado temporariamente
- **Scores locais:** Se IA offline, usa `ai_toxic_score`, `ai_divulgacao_score`, `ai_similarity_score`
- **Intenção PT:** Entende "cala a boca", "bane esse", "expulsa" em português

Comando `/resetai` limpa blacklist e recarrega providers.

## 🛡️ Moderação

- **Anti-Link:** Bloqueia domínios não permitidos (`allowed_links`)
- **Anti-Divulgação:** IA detecta promo, cassino, pix, etc
- **Anti-Spam:** Similaridade > 85% com últimas 5 msgs
- **Anti-Flood:** Limite por janela (com modo pico rígido)
- **Anti-Mention:** 5+ @ por msg ou 8 em 30s
- **Night Mode:** `silent` (apaga tudo) ou `strict` (só msg longa)

## 📜 Comandos
