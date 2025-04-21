import os
import requests
import subprocess
import time
from datetime import datetime
from influxdb_client import InfluxDBClient, Point, WriteOptions
from influxdb_client.client.write_api import SYNCHRONOUS
from dotenv import load_dotenv
import threading
import reactivex

load_dotenv()

# Configuração
INFLUXDB_URL = os.getenv("INFLUXDB_URL")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET")
INTERVAL_SECONDS = int(os.getenv("AGENT_INTERVAL_SECONDS", "10"))

# Sites para testar
TEST_SITES = [
    {
        "name": "google",
        "ping_target": "google.com",
        "page_load_url": "https://google.com"
    },
    {
        "name": "youtube",
        "ping_target": "youtube.com",
        "page_load_url": "https://youtube.com"
    },
    {
        "name": "rnp",
        "ping_target": "rnp.br",
        "page_load_url": "https://rnp.br"
    }
]

# Cabeçalhos da requisição para page load
HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'
}

# Valida as variáveis de ambiente
required_vars = {
    "INFLUXDB_URL": INFLUXDB_URL,
    "INFLUXDB_TOKEN": INFLUXDB_TOKEN,
    "INFLUXDB_ORG": INFLUXDB_ORG,
    "INFLUXDB_BUCKET": INFLUXDB_BUCKET
}

missing_vars = [var for var, value in required_vars.items() if value is None]
if missing_vars:
    raise EnvironmentError(f"Missing environment variables: {', '.join(missing_vars)}")

def ping_test(host, count=4):
    """Realiza o teste de ping e retorna a latência e perda de pacotes"""
    # Detect OS and adapt ping command
    if os.name == 'nt': # Windows
        command = ["ping", "-n", str(count), "-w", "5000", host] # 5 sec timeout
        loss_keyword = "Lost = "
        loss_index = -1
        rtt_keyword = "Average = "
        rtt_suffix = "ms"
    else: # Linux/macOS
        command = ["ping", "-c", str(count), "-W", "5", host] # 5 sec timeout
        loss_keyword = "packet loss"
        loss_index = -1
        rtt_keyword = "rtt min/avg/max/mdev ="
        rtt_suffix = " ms"

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=15 # Overall process timeout
        )

        output = result.stdout
        # print(f"Ping Output for {host}:\n{output}") # Debugging output

        loss = 100.0
        rtt_avg = 0.0
        success = False

        # Extract loss
        for line in output.splitlines():
            if loss_keyword in line:
                try:
                    loss_str = line.split('%')[0].split()[loss_index]
                    loss = float(loss_str)
                    break
                except (IndexError, ValueError) as e:
                    print(f"Error parsing loss for {host}: {e} in line: {line}")
                    pass # Use default loss=100.0

        # Extract RTT avg
        if os.name == 'nt':
            for line in output.splitlines():
                 if rtt_keyword in line:
                    try:
                        rtt_str = line.split(rtt_keyword)[1].split(rtt_suffix)[0]
                        rtt_avg = float(rtt_str)
                        break
                    except (IndexError, ValueError, TypeError) as e:
                        print(f"Error parsing RTT for {host} (Win): {e} in line: {line}")
                        pass # Use default rtt=0.0
        else:
            for line in output.splitlines():
                if rtt_keyword in line:
                    try:
                        rtt_parts = line.split('=')[1].split(rtt_suffix)[0].split('/')
                        rtt_avg = float(rtt_parts[1])
                        break
                    except (IndexError, ValueError) as e:
                         print(f"Error parsing RTT for {host} (Unix): {e} in line: {line}")
                         pass # Use default rtt=0.0

        # Determine success based on results
        success = result.returncode == 0 and loss < 100.0

        print(f"Ping test - Target: {host}, Loss: {loss}%, RTT: {rtt_avg:.2f}ms, Success: {success}")
        return {
            "target": host,
            "loss": loss,
            "rtt_avg": rtt_avg,
            "success": success
        }

    except subprocess.TimeoutExpired:
        print(f"Ping timeout for {host}")
        return {"target": host, "loss": 100.0, "rtt_avg": 0.0, "success": False}
    except FileNotFoundError:
        print(f"Error: 'ping' command not found. Please install iputils-ping or equivalent.")
        # Return failure but allow agent to continue trying other tests/sites
        return {"target": host, "loss": 100.0, "rtt_avg": 0.0, "success": False}
    except Exception as e:
        print(f"Ping test failed for {host}: {str(e)}")
        return {"target": host, "loss": 100.0, "rtt_avg": 0.0, "success": False}

def page_load_test(url):
    """Testa o tempo de carregamento da página e o código de status HTTP"""
    try:
        start_time = time.time()
        response = requests.get(url, headers=HEADERS, timeout=15) # Increased timeout
        load_time = time.time() - start_time

        print(f"Page load test - URL: {url}, Time: {load_time:.2f}s, Status: {response.status_code}")
        return {
            "url": url,
            "load_time": load_time,
            "status_code": response.status_code,
            "success": 200 <= response.status_code < 300 # Consider 2xx as success
        }
    except requests.exceptions.Timeout:
         print(f"Page load test timeout for {url}")
         return {"url": url, "load_time": 0.0, "status_code": 0, "success": False}
    except requests.exceptions.RequestException as e:
        print(f"Page load test failed for {url}: {str(e)}")
        return {"url": url, "load_time": 0.0, "status_code": 0, "success": False}
    except Exception as e:
        print(f"Unexpected error during page load test for {url}: {str(e)}")
        return {"url": url, "load_time": 0.0, "status_code": 0, "success": False}

# Função auxiliar para executar testes de um único site (para threading)
def test_single_site(site_config, results_list):
    site_name = site_config["name"]
    print(f"-- [Thread] Testing site: {site_name} --")
    site_results = [] # Resultados para este site específico

    # Teste de ping
    if "ping_target" in site_config:
        ping_result = ping_test(site_config["ping_target"])
        ping_result["test_type"] = "ping"
        ping_result["site_name"] = site_name
        site_results.append(ping_result)
    else:
        print(f"Skipping ping test for {site_name} (no target defined)")

    # Teste de carregamento de página
    if "page_load_url" in site_config:
        page_load_result = page_load_test(site_config["page_load_url"])
        page_load_result["test_type"] = "page_load"
        page_load_result["site_name"] = site_name
        site_results.append(page_load_result)
    else:
         print(f"Skipping page load test for {site_name} (no URL defined)")

    # Adiciona os resultados deste site à lista compartilhada
    # A operação append() em listas Python é considerada atomic (thread-safe)
    results_list.extend(site_results)
    print(f"-- [Thread] Finished testing site: {site_name} --")

def run_network_tests_concurrent():
    """Executa todos os testes de rede para cada site CONCORRENTEMENTE usando threads"""
    print("Running network tests concurrently...")
    threads = []
    all_results = [] # Lista para coletar resultados de todas as threads

    for site in TEST_SITES:
        # Cria e inicia uma thread para cada site
        thread = threading.Thread(target=test_single_site, args=(site, all_results))
        threads.append(thread)
        thread.start()

    # Espera todas as threads terminarem
    print(f"Waiting for {len(threads)} test threads to complete...")
    for thread in threads:
        thread.join()
    print("All test threads completed.")

    return all_results

def save_to_influx(test_results):
    """Salva os resultados dos testes de rede no InfluxDB usando BATCHING"""
    if not test_results:
        print("No network test results to save.")
        return

    # Configura opções de BATCHING
    write_options = WriteOptions(
        batch_size=50, # Tamanho menor de batch para testes de site
        flush_interval=5_000, # 5 segundos
        jitter_interval=2_000,
        retry_interval=5_000
    )

    try:
        with InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG) as client:
            buckets_api = client.buckets_api()
            if not buckets_api.find_bucket_by_name(INFLUXDB_BUCKET):
                print(f"Bucket '{INFLUXDB_BUCKET}' not found. Please create it.")
                return

            # Usa as opções de BATCHING
            write_api = client.write_api(write_options=write_options)
            records = []
            now = datetime.utcnow() # Pega o timestamp uma vez para todos os pontos do ciclo

            for test in test_results:
                try:
                    point = Point("network_tests") \
                        .tag("site", test["site_name"]) \
                        .tag("test_type", test["test_type"]) \
                        .time(now) # Usa o mesmo timestamp

                    if test["test_type"] == "ping":
                        point.tag("target", test["target"]) \
                             .field("loss", float(test["loss"])) \
                             .field("rtt_avg", float(test["rtt_avg"])) \
                             .field("success", float(test["success"])) # Converte bool para float
                    elif test["test_type"] == "page_load":
                        point.tag("url", test["url"]) \
                             .field("load_time", float(test["load_time"])) \
                             .field("status_code", int(test["status_code"])) \
                             .field("success", float(test["success"])) # Converte bool para float

                    records.append(point)

                except Exception as test_error:
                    print(f"Error processing test result for {test.get('site_name', 'unknown')} ({test.get('test_type', 'unknown')}): {str(test_error)}")
                    continue

            if records:
                print(f"Writing {len(records)} network test points to InfluxDB via BATCHING...")
                write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=records)
                print(f"Batch write initiated for {len(records)} points.")
            else:
                print("No valid network test records prepared to write")

            # Garante que o buffer de escrita seja esvaziado ao final
            print("Flushing write buffer...")
            write_api.flush()
            print("Write buffer flushed.")

    except Exception as e:
        print(f"Error connecting to or writing to InfluxDB: {str(e)}")

if __name__ == "__main__":
    print("Starting Network Sites Monitoring Agent (with Batching and Threading)")
    print(f"Config: InfluxDB={INFLUXDB_URL}, Interval={INTERVAL_SECONDS}s")
    print(f"Testing Sites: {', '.join([s['name'] for s in TEST_SITES])}")

    while True:
        start_time = time.time()
        print(f"Network Test Cycle started at {datetime.now().isoformat()}")

        results = [] # Initialize results
        try:
            # Chama a função concorrente
            results = run_network_tests_concurrent()

            if results:
                save_to_influx(results)
            else:
                 print("Skipping InfluxDB write as no test results were generated.")

        except Exception as e:
            print(f"Critical error in Sites main loop: {str(e)}")
            # Optionally add more robust error handling/logging here

        # Calcula o tempo restante para o próximo ciclo
        elapsed = time.time() - start_time
        sleep_time = max(0, INTERVAL_SECONDS - elapsed)
        print(f"Network Test Cycle completed in {elapsed:.2f}s. Next run in {sleep_time:.2f}s")
        time.sleep(sleep_time)