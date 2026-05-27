import streamlit as st
import numpy as np
from scipy.stats import norm

class OptionCalculator:
    def __init__(self):
        st.title("📊 Optionsschein-Rechner (Black-Scholes)")

        self.option_type = st.selectbox("Optionstyp wählen", [ "Put", "Call"])
        self.S = st.number_input("Aktueller Kurs des Basiswerts (S)", value=100.0)
        self.K = st.number_input("Strike-Preis (K)", value=100.0)
        self.T = st.number_input("Restlaufzeit in Jahren (T)", value=0.5)
        self.r = st.number_input("Risikofreier Zinssatz (r) in %", value=2.0) / 100
        self.sigma = st.number_input("Volatilität (σ) in %", value=20.0) / 100

        if st.button("Berechnen"):
            price = self.calculate_option_price()
            st.success(f"Der {self.option_type}-Preis beträgt: {price:.2f} €")

    def calculate_option_price(self):
        S, K, T, r, sigma = self.S, self.K, self.T, self.r, self.sigma

        d1 = (np.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * np.sqrt(T))
        d2 = d1 - sigma * np.sqrt(T)

        if self.option_type == "Call":
            price = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)
        else:  # Put
            price = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

        return price

## Ausführung in Streamlit
#if __name__ == "__main__":
#    OptionCalculator()
