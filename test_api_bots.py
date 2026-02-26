
import requests
import json

try:
    print("Testando conexão com http://127.0.0.1:8510/api/bots ...")
    response = requests.get("http://127.0.0.1:8510/api/bots", timeout=5)
    
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        print(f"Número de bots retornados: {len(data)}")
        if len(data) > 0:
            print("Exemplo de bot:")
            print(json.dumps(data[0], indent=2))
        else:
            print("AVISO: Lista de bots vazia retornada pela API.")
    else:
        print(f"Erro na API: {response.text}")

except Exception as e:
    print(f"Erro ao conectar: {e}")
