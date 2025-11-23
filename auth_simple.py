import streamlit as st
import hmac

def check_password():
    """Verifică parola - o singură parolă pentru toată aplicația"""
    
    def password_entered():
        """Verifică dacă parola introdusă este corectă"""
        if hmac.compare_digest(st.session_state["password"], st.secrets["password"]):
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # Șterge parola din memorie
        else:
            st.session_state["password_correct"] = False

    # Dacă parola a fost deja verificată corect, returnează True
    if st.session_state.get("password_correct", False):
        return True

    # Afișează formular de login
    st.markdown("### 🔐 Autentificare Aplicație")
    st.text_input(
        "Introdu parola:", 
        type="password", 
        on_change=password_entered, 
        key="password",
        placeholder="Parola aplicației"
    )
    
    if "password_correct" in st.session_state:
        st.error("❌ Parolă incorectă")
    
    return False
