# 📞 Dashboard de Acompanhamento Pós-Instalação

> Dashboard que mede a **qualidade do contato pós-venda** de uma ISP: quais clientes recém-instalados foram contatados, quem ficou esquecido, e como isso se relaciona com churn — lendo direto do sistema de chamados (IXC).

![Python](https://img.shields.io/badge/Python-3.11+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-dashboard-000000?logo=flask&logoColor=white)
![MySQL](https://img.shields.io/badge/MySQL-IXC-4479A1?logo=mysql&logoColor=white)

---

## 🎯 O problema

Cliente recém-instalado é o mais frágil: se ninguém faz o contato de acompanhamento nos primeiros dias, a chance de cancelamento dispara. Mas a equipe não tinha visibilidade de **quem já foi contatado e quem não**, nem de quais bairros ou empresas de instalação concentravam mais problemas.

## 💡 A solução

Um dashboard que lê os chamados (`su_ticket`) do IXC e transforma em indicadores de operação:

- **Taxa de contato pós-ativação** — quantos dos novos clientes foram efetivamente contatados (meta configurável).
- **Clientes sem contato** — lista de quem foi instalado e ainda não recebeu acompanhamento.
- **Motivos de contato** — ranking dos motivos mais frequentes no período.
- **Recorte por bairro e por empresa de instalação** — onde os problemas se concentram.
- **Exportação CSV** — para relatórios e cruzamentos externos.

---

## 🛠️ Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python + **Flask** |
| Fonte de dados | **MySQL** (IXC — tabela `su_ticket`) |
| Coleta | Threading + cache em memória |
| Export | CSV nativo |

---

## 🚀 Rodando localmente

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # preencha a conexão MySQL (IXC)
python app.py                 # http://localhost:5009
```

---

<sub>Dashboard interno de uma ISP regional. Credenciais e dados reais foram removidos desta versão de portfólio.</sub>
