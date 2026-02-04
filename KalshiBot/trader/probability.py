#!/usr/bin/env python3
"""
Probability Calculator - Fair Value Engine

Uses Black-Scholes model for binary options (digital calls/puts)
to calculate the fair probability of an event occurring.
"""

import math
import logging
from scipy.stats import norm

logger = logging.getLogger(__name__)


class ProbabilityCalculator:
    """Calculates fair value probabilities for binary events."""
    
    def __init__(self, default_volatility: float = 0.60, risk_free_rate: float = 0.04):
        """
        Args:
            default_volatility: Annualized volatility (e.g., 0.60 for 60%)
            risk_free_rate: Annualized risk-free interest rate
        """
        self.volatility = default_volatility
        self.risk_free_rate = risk_free_rate
    
    def btc_above_strike(
        self, 
        current_price: float, 
        strike_price: float, 
        hours_to_expiry: float, 
        volatility: float = None
    ) -> float:
        """
        Calculate probability that BTC > strike at expiry.
        Equivalent to the price of a binary call option.
        
        Args:
            current_price: Current underlying price (e.g., BTC limit price)
            strike_price: The target price
            hours_to_expiry: Time remaining in hours
            volatility: Optional override for volatility
            
        Returns:
            Probability 0.0 to 1.0
        """
        if hours_to_expiry <= 0:
            return 1.0 if current_price > strike_price else 0.0
            
        S = current_price
        K = strike_price
        T = hours_to_expiry / 24 / 365  # Convert hours to years
        r = self.risk_free_rate
        sigma = volatility if volatility else self.volatility
        
        # d2 term in Black-Scholes is used for binary call probability
        # For a digital call (pays $1 if S > K), price = e^(-rT) * N(d2)
        # d2 = (ln(S/K) + (r - 0.5 * sigma^2) * T) / (sigma * sqrt(T))
        
        sqrt_T = math.sqrt(T)
        d2 = (math.log(S / K) + (r - 0.5 * sigma**2) * T) / (sigma * sqrt_T)
        
        # N(d2) is the probability S_T > K in the risk-neutral measure
        probability = norm.cdf(d2)
        
        # Discount factor (optional for short term, but required for strict fair value)
        # fair_value = math.exp(-r * T) * probability
        
        # For prediction markets, we generally trade on the probability itself
        return probability

    def btc_below_strike(self, current_price: float, strike_price: float, hours_to_expiry: float) -> float:
        """Calculate probability BTC < strike (Binary Put)."""
        return 1.0 - self.btc_above_strike(current_price, strike_price, hours_to_expiry)

    def implied_volatility(self, market_price: float, current_price: float, strike_price: float, hours_to_expiry: float) -> float:
        """
        Calculate implied volatility given a market price.
        (Simplified binary search)
        """
        low = 0.1
        high = 3.0
        
        for _ in range(20):
            mid = (low + high) / 2
            est_price = self.btc_above_strike(current_price, strike_price, hours_to_expiry, mid)
            
            if est_price > market_price:
                low = mid
            else:
                high = mid
                
        return (low + high) / 2
