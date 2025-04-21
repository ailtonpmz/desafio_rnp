import os
import requests
import time
from datetime import datetime
from influxdb_client import InfluxDBClient, Point, WriteOptions
from influxdb_client.client.write_api import SYNCHRONOUS
from dotenv import load_dotenv
import reactivex

load_dotenv()

# Configuração
API_URL = os.getenv("VIAIPE_API_URL")
INFLUXDB_URL = os.getenv("INFLUXDB_URL")
INFLUXDB_TOKEN = os.getenv("INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("INFLUXDB_ORG")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET")
INTERVAL_SECONDS = int(os.getenv("AGENT_INTERVAL_SECONDS", "10"))

# Cabeçalhos da requisição
HEADERS = {
    'accept': 'application/json',
    'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36'
}

# Valida as variáveis de ambiente
required_vars = {
    "VIAIPE_API_URL": API_URL,
    "INFLUXDB_URL": INFLUXDB_URL,
    "INFLUXDB_TOKEN": INFLUXDB_TOKEN,
    "INFLUXDB_ORG": INFLUXDB_ORG,
    "INFLUXDB_BUCKET": INFLUXDB_BUCKET
}

missing_vars = [var for var, value in required_vars.items() if value is None]
if missing_vars:
    raise EnvironmentError(f"Missing environment variables: {', '.join(missing_vars)}")

def fetch_data():
    """Busca dados da API ViaIpe"""
    try:
        print(f"Fetching data from API: {API_URL}")
        response = requests.get(API_URL, headers=HEADERS, timeout=20) # Increased timeout
        response.raise_for_status()
        locations = response.json()

        if not locations or len(locations) == 0:
            print("Warning: Empty response from API")
            return None

        print(f"Found {len(locations)} locations in the API")
        return locations

    except requests.exceptions.Timeout:
        print(f"Error fetching data (Timeout Error)")
        return None
    except requests.exceptions.RequestException as req_e:
        print(f"Error fetching data (Request Error): {req_e}")
        return None
    except Exception as e:
        print(f"Unexpected error fetching data: {str(e)}")
        return None


def save_to_influx(locations):
    """Salva as métricas do ViaIpe no InfluxDB, separando interfaces."""
    if not locations:
        print("No location data to save.")
        return

    # Configura opções de BATCHING
    write_options = WriteOptions(
        batch_size=500,
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
            records_to_write = [] # Lista para acumular todos os pontos

            for location in locations:
                location_id = str(location.get('id', 'unknown'))
                location_name = str(location.get('name', 'unknown'))
                now = datetime.utcnow()

                try:
                    # Ponto 1: Dados da Localização (lat, lng, smoke)
                    location_point = Point("viaipe_location_metrics") \
                        .tag("location_id", location_id) \
                        .tag("location_name", location_name) \
                        .field("lat", float(location.get('lat', 0.0))) \
                        .field("lng", float(location.get('lng', 0.0))) \
                        .time(now)

                    # Adiciona dados de smoke se presentes
                    if 'data' in location and 'smoke' in location['data']:
                        smoke = location['data']['smoke']
                        location_point.field("smoke_loss", float(smoke.get('loss', 0.0))) \
                                    .field("smoke_avg_val", float(smoke.get('avg_val', 0.0))) \
                                    .field("smoke_max_loss", float(smoke.get('max_loss', 0.0))) \
                                    .field("smoke_val", float(smoke.get('val', 0.0))) \
                                    .field("smoke_max_val", float(smoke.get('max_val', 0.0))) \
                                    .field("smoke_avg_loss", float(smoke.get('avg_loss', 0.0)))

                    records_to_write.append(location_point)

                    # Ponto(s) 2: Dados de cada Interface
                    if 'data' in location and 'interfaces' in location['data']:
                        interfaces = location['data']['interfaces']
                        for interface in interfaces:
                            interface_name = str(interface.get('nome', 'unknown'))
                            interface_graph_id = str(interface.get('traffic_graph_id', 'unknown'))

                            interface_point = Point("viaipe_interface_metrics") \
                                .tag("location_id", location_id) \
                                .tag("location_name", location_name) \
                                .tag("interface_nome", interface_name) \
                                .tag("interface_graph_id", interface_graph_id) \
                                .tag("interface_tipo", str(interface.get('tipo', 'unknown'))) \
                                .tag("interface_client_side", str(interface.get('client_side', 'unknown'))) \
                                .field("interface_max_out", float(interface.get('max_out', 0.0))) \
                                .field("interface_max_traffic_up", float(interface.get('max_traffic_up', 0.0))) \
                                .field("interface_max_traffic_down", float(interface.get('max_traffic_down', 0.0))) \
                                .field("interface_avg_in", float(interface.get('avg_in', 0.0))) \
                                .field("interface_avg_out", float(interface.get('avg_out', 0.0))) \
                                .field("interface_traffic_in", float(interface.get('traffic_in', 0.0))) \
                                .field("interface_traffic_out", float(interface.get('traffic_out', 0.0))) \
                                .field("interface_max_in", float(interface.get('max_in', 0.0))) \
                                .time(now)

                            records_to_write.append(interface_point)

                except Exception as loc_error:
                    print(f"Error processing location {location_id}: {str(loc_error)}")
                    continue # Skip this location

            if records_to_write:
                print(f"Writing {len(records_to_write)} API points (locations+interfaces) to InfluxDB via BATCHING...")
                # Envia todos os pontos acumulados para a API de escrita em lote
                write_api.write(bucket=INFLUXDB_BUCKET, org=INFLUXDB_ORG, record=records_to_write)
                print(f"Batch write initiated for {len(records_to_write)} points.")
            else:
                print("No valid API records prepared to write")

            # Garante que o buffer de escrita seja esvaziado ao final
            print("Flushing write buffer...")
            write_api.flush()
            print("Write buffer flushed.")

    except Exception as e:
        print(f"Error connecting to or writing to InfluxDB: {str(e)}")


if __name__ == "__main__":
    print("Starting ViaIpe API Monitoring Agent (with Batching and Interface Separation)")
    print(f"Config: API={API_URL}, InfluxDB={INFLUXDB_URL}, Interval={INTERVAL_SECONDS}s")

    while True:
        start_time = time.time()
        print(f"API Collection started at {datetime.now().isoformat()}")

        api_data = None # Initialize api_data
        try:
            api_data = fetch_data()

            if api_data:
                 save_to_influx(api_data)
            else:
                print("Skipping InfluxDB write due to fetch error or empty data.")

        except Exception as e:
            print(f"Critical error in API main loop: {str(e)}")

        # Calcula o tempo restante para o próximo ciclo
        elapsed = time.time() - start_time
        sleep_time = max(0, INTERVAL_SECONDS - elapsed)
        print(f"API Collection completed in {elapsed:.2f}s. Next run in {sleep_time:.2f}s")
        time.sleep(sleep_time)