import sys
from pathlib import Path

# Add root to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.oracle import PriceOracle
import db
import config
import math

def test_bayesian_math():
    print("--- Testing Bayesian Math ---")
    p_prior = 0.5
    
    # Positive movement (+1%) should increase probability
    p_post_up = PriceOracle.apply_bayesian_update(p_prior, 0.01)
    print(f"Prior: 0.5 | Movement: +1% | Post: {p_post_up:.4f}")
    assert p_post_up > p_prior
    
    # Negative movement (-1%) should decrease probability
    p_post_down = PriceOracle.apply_bayesian_update(p_prior, -0.01)
    print(f"Prior: 0.5 | Movement: -1% | Post: {p_post_down:.4f}")
    assert p_post_down < p_prior
    
    # Extreme movement
    p_post_extreme = PriceOracle.apply_bayesian_update(p_prior, 0.10)
    print(f"Prior: 0.5 | Movement: +10% | Post: {p_post_extreme:.4f}")

def test_kelly_sizing():
    print("\n--- Testing Kelly Sizing ---")
    p_yes = 0.65
    p_eff_yes = 0.60 # Entry price after fees/buffer
    
    # Kelly = (p - p_entry) / (1 - p_entry)
    k_expected = (p_yes - p_eff_yes) / (1 - p_eff_yes)
    print(f"P_yes: 0.65 | P_eff: 0.60 | Kelly Pure: {k_expected:.4f}")
    
    # Fractional Kelly (0.25)
    f_k = k_expected * 0.25
    print(f"Fractional Kelly (0.25): {f_k:.4f}")
    
    # Position size for $10,000 bankroll
    amount = 10000 * f_k
    print(f"Suggested Amount for $10k: ${amount:.2f}")

def test_brier_score_logic():
    print("\n--- Testing Brier Score Logic ---")
    # Mocking trades for Brier Score
    # BS = (1/n) * sum((p - o)^2)
    
    predictions = [0.8, 0.2, 0.6, 0.4]
    outcomes = [1, 0, 1, 0] # All correct
    
    errors = [(p - o)**2 for p, o in zip(predictions, outcomes)]
    bs = sum(errors) / len(errors)
    print(f"Predictions: {predictions}")
    print(f"Outcomes: {outcomes}")
    print(f"BS (Correct): {bs:.4f}")
    
    # Incorrect predictions
    outcomes_bad = [0, 1, 0, 1]
    errors_bad = [(p - o)**2 for p, o in zip(predictions, outcomes_bad)]
    bs_bad = sum(errors_bad) / len(errors_bad)
    print(f"BS (Incorrect): {bs_bad:.4f}")

if __name__ == "__main__":
    test_bayesian_math()
    test_kelly_sizing()
    test_brier_score_logic()
    print("\n✅ Verification scripts defined.")
