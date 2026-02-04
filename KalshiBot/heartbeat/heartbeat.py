#!/usr/bin/env python3
"""
Oracle Anti-Reclamation Heartbeat

Oracle Cloud's free tier can reclaim idle instances. This script maintains
minimal CPU activity to prevent automatic server shutdown.

Technique: Perform lightweight computation every 30 seconds to register
as "active" without consuming meaningful resources.
"""

import time
import hashlib
import logging
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [HEARTBEAT] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def compute_heartbeat() -> str:
    """
    Perform a trivial computation to generate CPU activity.
    Returns a hash to prove work was done.
    """
    data = str(datetime.utcnow().timestamp()).encode()
    return hashlib.sha256(data).hexdigest()[:8]


def main():
    """
    Main heartbeat loop. Runs forever, logging activity every 30 seconds.
    """
    logger.info("🫀 Heartbeat started - Oracle anti-reclamation active")
    
    iteration = 0
    while True:
        try:
            # Perform minimal work
            proof = compute_heartbeat()
            iteration += 1
            
            # Log every 10 minutes (20 iterations) to avoid log spam
            if iteration % 20 == 0:
                logger.info(f"💓 Alive | Iteration: {iteration} | Proof: {proof}")
            
            # Sleep 30 seconds between heartbeats
            time.sleep(30)
            
        except KeyboardInterrupt:
            logger.info("🛑 Heartbeat stopped by user")
            break
        except Exception as e:
            logger.error(f"❌ Heartbeat error: {e}")
            time.sleep(5)  # Brief pause before retry


if __name__ == "__main__":
    main()
