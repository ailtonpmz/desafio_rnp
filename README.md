# Desafio RNP - Monitoramento ViaIpe e Rede

Este projeto implementa uma solução de monitoramento que coleta e visualiza dados de desempenho de rede e métricas operacionais da API ViaIpe da RNP (https://viaipe.rnp.br/api/norte).

## Visão Geral da Arquitetura

A solução é composta por quatro componentes principais orquestrados via Docker Compose:

1.  **Agente API (`agent_api`)**: Um script Python containerizado responsável por coletar dados da API ViaIpe.
2.  **Agente Sites (`agent_sites`)**: Um script Python containerizado responsável por executar testes de rede periódicos.
3.  **Banco de Dados Time-Series (`influxdb`)**: Uma instância do InfluxDB para armazenar as métricas coletadas pelos agentes ao longo do tempo.
4.  **Plataforma de Visualização (`grafana`)**: Uma instância do Grafana configurada para se conectar ao InfluxDB e exibir os dados em dashboards interativos.

## Componentes Detalhados

### 1. Agente API (`agent/agent_api/`)

*   **Script**: `agent_api.py`
*   **Tecnologia**: Python 3, `requests`, `influxdb-client`, `python-dotenv`.
*   **Funcionalidades**:
    *   Consulta a API ViaIpe (`VIAIPE_API_URL`) para obter dados operacionais de localidades (incluindo dados de interfaces e testes de ping internos da RNP).
    *   Envia os dados coletados da ViaIpe para o InfluxDB (measurement: `viaipe_metrics`).
    *   Opera em um loop contínuo com intervalo configurável (`AGENT_INTERVAL_SECONDS`).
*   **Configuração**: Através de variáveis de ambiente definidas no arquivo `.env` (`VIAIPE_API_URL`, InfluxDB vars, `AGENT_INTERVAL_SECONDS`).

### 2. Agente Sites (`agent/agent_sites/`)

*   **Script**: `agent_sites.py`
*   **Tecnologia**: Python 3, `requests`, `influxdb-client`, `python-dotenv`, `subprocess` (para ping).
*   **Funcionalidades**:
    *   Executa testes de `ping` (latência RTT e perda de pacotes %) para `google.com`, `youtube.com`, `rnp.br`.
    *   Executa testes de tempo de carregamento de página (tempo e código de status HTTP) para os mesmos sites.
    *   Envia os resultados dos testes de rede para o InfluxDB (measurement: `network_tests`).
    *   Opera em um loop contínuo com intervalo configurável (`AGENT_INTERVAL_SECONDS`).
*   **Configuração**: Através de variáveis de ambiente definidas no arquivo `.env` (InfluxDB vars, `AGENT_INTERVAL_SECONDS`).

### 3. InfluxDB (`influxdb`)

*   **Tecnologia**: Imagem Docker `influxdb:latest`.
*   **Funcionalidades**:
    *   Armazena dados de séries temporais enviados pelos agentes (`viaipe_metrics` e `network_tests`).
    *   Persiste os dados utilizando um volume Docker (`influxdb-data`).
    *   Configurado automaticamente na inicialização (usuário, senha, organização, bucket) via variáveis de ambiente do `.env`.
*   **Bucket Principal**: Definido por `INFLUXDB_BUCKET` no `.env`.

### 4. Grafana (`grafana`)

*   **Tecnologia**: Imagem Docker `grafana/grafana:latest`.
*   **Funcionalidades**:
    *   Fornece uma interface web para visualização dos dados.
    *   Conecta-se ao InfluxDB como fonte de dados (configurado via `grafana/provisioning/datasources`).
    *   Exibe dashboards pré-configurados (provisionados via `grafana/provisioning/dashboards`), incluindo:
        *   Painéis com métricas de testes de rede (ping, page load).
        *   Painéis detalhados por localidade ViaIpe, mostrando Capacidade, Consumo Médio de Banda (Mbps), Disponibilidade (%), Latência e um índice de Qualidade (%).
        *   Um painel Geomap visualizando as localidades ativas no mapa, com marcadores indicando Capacidade e informações adicionais nos tooltips.
    *   Realiza cálculos e agregações sobre os dados brutos diretamente nos dashboards usando consultas Flux (ex: cálculo de Disponibilidade, Qualidade, Consumo Mbps).
    *   Filtra a lista de localidades selecionáveis para mostrar apenas aquelas com dados recentes.
    *   Persiste suas configurações e dashboards customizados via volume Docker (`grafana-data`).
*   **Acesso**: Disponível na porta `3001` do host.

As imagens abaixo ilustram exemplos dos dashboards:
![Dashboard Grafana mostrando Localidades ViaIpe](Print/Captura%20de%20tela%202025-04-19%20023121.png)
![Dashboard Grafana mostrando Testes de Rede](Print/Captura%20de%20tela%202025-04-19%20023348.png)


## Fluxo de Dados

1.  Os **Agentes** (`agent_api` e `agent_sites`) executam suas tarefas de coleta (API ViaIpe e testes de rede) em paralelo e em intervalos regulares.
2.  Os dados coletados são formatados e enviados para o **InfluxDB** em seus respectivos *measurements*.
3.  O **InfluxDB** armazena esses dados como séries temporais.
4.  O **Grafana** consulta o **InfluxDB** periodicamente (ou sob demanda do usuário) para buscar os dados.
5.  O **Grafana** processa (quando necessário, via Flux) e exibe os dados nos dashboards configurados.
6.  O **Usuário** acessa a interface web do **Grafana** para monitorar o status e o desempenho.

## Configuração e Execução

1.  **Pré-requisitos**: Docker e Docker Compose instalados.
2.  **Configuração**: Crie o arquivo `.env` na raiz do projeto definindo as seguintes variáveis (remova quaisquer aspas ao redor dos valores):
    *   `VIAIPE_API_URL`: URL da API ViaIpe a ser consultada (ex: `https://viaipe.rnp.br/api/norte`).
    *   `INFLUXDB_URL`: URL de acesso ao InfluxDB (use `http://influxdb:8086` para comunicação interna do Docker).
    *   `INFLUXDB_TOKEN`: Token de API para autenticação no InfluxDB.
    *   `INFLUXDB_ORG`: Nome da organização no InfluxDB.
    *   `INFLUXDB_BUCKET`: Nome do bucket no InfluxDB para armazenar os dados.
    *   `AGENT_INTERVAL_SECONDS`: Intervalo (em segundos) entre as coletas dos agentes (padrão: 10).
    *   `INFLUXDB_USERNAME`: Usuário inicial do InfluxDB (para setup).
    *   `INFLUXDB_PASSWORD`: Senha inicial do InfluxDB (para setup).
3.  **Execução**:

    ```bash
    # Construir as imagens (ou reconstruir após alterações)
    docker compose build
    ```

    ```bash
    # Iniciar todos os serviços em background
    docker compose up -d
    ```
4.  **Acesso**:
    *   Grafana: `http://localhost:3001`
        *   User: `admin`
        *   Senha: `admin` (padrão do Grafana)

    *   Acesso à UI do InfluxDB: `http://localhost:8086`
        *   User: `admin` (ou o `INFLUXDB_USERNAME` definido no `.env`)
        *   Senha: `adminadmin@` (ou a `INFLUXDB_PASSWORD` definida no `.env`)

## Observações

*   Os cálculos mais complexos sobre os dados (como Disponibilidade média, Consumo médio em Mbps, índice de Qualidade) são realizados diretamente nas consultas Flux dentro dos painéis do Grafana, não nos agentes.
*   A variável de seleção de localidades no Grafana é configurada para exibir apenas localidades que reportaram dados recentemente, ocultando automaticamente aquelas sem dados ("No data").
*   Consulte os logs dos contêineres (`docker compose logs agent_api`, `docker compose logs agent_sites`) para diagnosticar problemas.


---------------------
https://github.com/ailtonpmz/CI-AzureDevops
https://github.com/ailtonpmz/Tofu_TJ_Monitoramento
https://github.com/ailtonpmz/eSM_ansible



