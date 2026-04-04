# NASUgreen — Media Repository Pipeline 🎬📸

O **NASUgreen** trata-se de um robusto pipeline e dashboard web escrito em Python puro para extração, consolidação geocronológica e limpeza segura de mídias (fotos e vídeos).

Originalmente idealizado para rodar diretamente via Docker em sistemas NAS corporativos ou domésticos — como NAS **UGREEN** e **Synology** —, o projeto escaneia múltiplas pastas de origem, extrai metadados nativamente, detecta duplicatas com verificação segura via Hash (SHA256) e transfere tudo para uma organização limpa por *Ano/Mês*.

Vem com um **painel de controle (Dashboard) embutido no navegador** onde você controla todo o fluxo — desde a aprovação da varredura até a lixeira interativa de cópias processadas e navegação em tempo real dos seus álbuns em tamanho real (Lightbox).

---

## 🎯 Principais Funcionalidades

- **⚙️ Organização Automática:** Vasculha pastas de entrada (`/input`), busca dados como a data de captura (via EXIF nativo em imagens nativas e fallback de timestamp) e move ou copia perfeitamente para (`/output/YYYY/MM`).
- **🛡️ Deduplicação Extremamente Segura:** Evita fotos idênticas transferidas acidentalmente por celulares diversos. Se diferentes fotos gerarem o mesmo *Hash SHA-256*, a dupla será categorizada no banco interno SQLite e evitada.
- **🖼️ Dashboard e Visualização (Lightbox):** Acesse a porta `9090` e utilize a UI que informa o real estado do processo (Polling Progress) do seu Pipeline. Funcionalidades como a visualização em grade (Thumbnail API) e expansão em Lightbox (Seta/Teclado/Swipe).
- **🧹 Limpeza Cirúrgica (Dry-Run / Real):** Uma ferramenta inteligente capaz de interagir com as pastas de `/input`. Ela **NUNCA deleta acidentalmente** — ela re-valida no SQLite e recalcula hashes se a foto realmente existe na pasta `/output` de destino e possui o exato mesmo tamanho. Permite visualização ("simulação") passo a passo do log do que acontecerá antes de realizar o `unlink` real.
- **🐳 Multi-Architecture em Docker:** Pronto para rodar não apenas em servidores `linux/amd64`, mas distribuído nativamente para processadores ARM64 (como os encontrados em NAS e Raspberry Pi). Sem requerer pacotes complexos do APT no ambiente final.

---

## 🚀 Como instalar e rodar com Docker (Recomendado)

A infraestrutura e execução fica a cargo do Docker e os seus respectivos mapeamentos de pastas (`volumes`).

Exemplo do seu `docker-compose.yaml`:
```yaml
services:
  nasugreen:
    image: cleibersilva/nasugreen:4.2.4
    container_name: nasugreen
    restart: unless-stopped
    ports:
      - "9090:9090"
    volumes:
      # Mount da(s) pasta(s) repletas de bagunça vindas de backups:
      - ./input:/input
      # Mount da pasta do seu Álbum oficial (onde ficaram guardadas e limpas):
      - ./output:/output
      # Suas possíveis customizações ao invés do padrão do container
      - ./config.docker.yaml:/config/config.yaml:ro
    environment:
      - TZ=America/Sao_Paulo
```

Use `docker compose pull` para baixar a imagem (certifique-se de usar a imagem da versão desejada, como `4.2.4` ou `latest`), seguida de `docker compose up -d` para isolá-la no background e mantê-la segura contra paradas.

**ATENÇÃO:** Para que a função 🧹 **Limpar Entrada** funcione, a declaração do volume `- ./input:/input` no docker-compose **Não** deve conter o sufixo readonly (`:ro`). Ele deve ser mapeado em formato regular de gravação (w/r) para que seu clique de "Limpeza" consiga se comunicar com a deleção pós-segurança real.

## 🖥 O Dashboard na Web
Acesse: `http://{IP_DO_NAS_OU_LOCALHOST}:9090`

1. **Dashboard principal:** Mostra o console do pipeline varrendo arquivos ou o relógio e eventos ao vivo emitidos periodicamente pelo backend sem bloquear outras instâncias.
2. **Explorador Visual (Álbum):** Visite a aba **Entrada** e **Saída** para rodar pelo emaranhado de arquivos e observe as pastas. Clique sobre a imagem para navegar em tamanho completo.
3. **Limpeza inteligente:** Execute "Simulações" seguras com listagem do motivo do pulo de arquivos em Log, garantindo a sua tranquilidade antes de comandar a "Limpeza Real".

---

## 🛠️ Para Desenvolvedores (Rodar local ou Testes)

Crie um ambiente local em Python puro (`3.11+`):

```bash
# Executa servidor web puro simulando o pipeline
python3 -m web.server --config config.local.yaml --host 127.0.0.1 --port 9090

# Para rodar as suítes de testes unitários (120+ cenários)
pip install pytest
pytest tests/
```

### Principais Bibliotecas do Core:
- `http.server`: O núcleo de nosso frontend dashboard, totalmente agnóstico de frameworks restritivos.
- `sqlite3`: Mantém sob forte consistência o estado dos arquivos observados evitando redundâncias sem sentido de I/O em disco.
- `threading/ThreadPoolExecutor`: Varredura paraleizada por N-Threads que agiliza o processo de reconhecimento na raiz. 

---
Feito de forma nativa e enxuta nas entranhas tecnológicas para durar anos com você — desenvolvido inteiramente pensando na sua automação de mídia digital!
