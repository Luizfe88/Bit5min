import sys
import os

# Adiciona o diretório atual ao sys.path para importar config
sys.path.append(os.getcwd())

import config

print(f"MARKET_FILTER threshold: {config.MARKET_FILTER['institutional_volume_threshold']}")
print(f"Getter threshold: {config.get_institutional_volume_threshold()}")

try:
    assert config.MARKET_FILTER['institutional_volume_threshold'] == 15000.0
    assert config.get_institutional_volume_threshold() == 15000.0
    print("Verification successful!")
except AssertionError as e:
    print(f"Verification FAILED: {e}")
    sys.exit(1)
