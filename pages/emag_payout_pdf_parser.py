"""
eMAG Payout PDF Parser
Extrage suma totală și lista facturilor din PDF-ul de payout eMAG
"""

import pdfplumber
import re
import hashlib
from typing import Dict, List, Tuple, Optional
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor


# ═══════════════════════════════════════════════════════
# EXTRAGERE TEXT DIN PDF
# ═══════════════════════════════════════════════════════

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """
    Extrage tot textul din PDF.
    
    Args:
        pdf_bytes: Bytes-urile fișierului PDF
        
    Returns:
        str: Tot textul din PDF, concatenat
    """
    full_text = ""
    
    with pdfplumber.open(pdf_bytes) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                full_text += text + "\n"
    
    return full_text


# ═══════════════════════════════════════════════════════
# EXTRAGERE SUMA TOTALĂ
# ═══════════════════════════════════════════════════════

def extract_total_amount(text: str) -> Optional[float]:
    """
    Extrage suma totală de plată din text.
    
    Caută pattern-uri de genul:
    - "Total de plata: 12.345,67 RON"
    - "Total amount: 12,345.67 RON"
    - "TOTAL: 12345.67"
    
    Args:
        text: Textul din PDF
        
    Returns:
        float: Suma totală sau None dacă nu găsește
    """
    # Pattern-uri posibile pentru total
    patterns = [
        r'Total\s+de\s+plata[:\s]+([0-9.,]+)\s*RON',
        r'Total\s+amount[:\s]+([0-9.,]+)\s*RON',
        r'TOTAL[:\s]+([0-9.,]+)\s*RON',
        r'Total[:\s]+([0-9.,]+)\s*RON',
        r'Suma\s+totala[:\s]+([0-9.,]+)\s*RON',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            amount_str = match.group(1)
            # Convertește din format românesc (12.345,67) sau internațional (12,345.67)
            # Elimină separatoare de mii și înlocuiește virgula cu punct
            amount_str = amount_str.replace('.', '').replace(',', '.')
            try:
                return float(amount_str)
            except ValueError:
                continue
    
    return None


# ═══════════════════════════════════════════════════════
# EXTRAGERE NUMERE FACTURI
# ═══════════════════════════════════════════════════════

def extract_invoices(text: str) -> List[Dict]:
    """
    Extrage toate numerele de facturi din text.
    
    Caută pattern-uri de genul:
    - C-MKTP-4990846
    - V-MKTP-1307946
    - Y-MKTP-325184
    - A-MKTP-978103
    
    Args:
        text: Textul din PDF
        
    Returns:
        List[Dict]: Lista cu dicționare {invoice_number, invoice_type, position, raw_line}
    """
    # Pattern pentru numere de facturi eMAG
    pattern = r'([A-Z]{1,4})-MKTP-(\d+)'
    
    invoices = []
    seen = set()  # Anti-duplicat în același PDF
    
    # Căutăm în fiecare linie pentru context
    lines = text.split('\n')
    
    for idx, line in enumerate(lines):
        matches = re.finditer(pattern, line)
        
        for match in matches:
            invoice_number = match.group(0)  # ex: C-MKTP-4990846
            invoice_type = match.group(1)    # ex: C
            
            # Skip duplicate în același PDF
            if invoice_number in seen:
                continue
            
            seen.add(invoice_number)
            
            # Încearcă să extragă suma dacă e pe aceeași linie
            amount = None
            amount_match = re.search(r'([0-9.,]+)\s*RON', line)
            if amount_match:
                amount_str = amount_match.group(1).replace('.', '').replace(',', '.')
                try:
                    amount = float(amount_str)
                except ValueError:
                    pass
            
            invoices.append({
                'invoice_number': invoice_number,
                'invoice_type': invoice_type,
                'invoice_amount': amount,
                'position_in_pdf': idx + 1,
                'raw_line': line.strip()
            })
    
    return invoices


# ═══════════════════════════════════════════════════════
# EXTRAGERE PAYOUT ID ȘI DATE
# ═══════════════════════════════════════════════════════

def extract_payout_info(text: str, filename: str) -> Dict:
    """
    Extrage informații despre payout (ID, date).
    
    Args:
        text: Textul din PDF
        filename: Numele fișierului (poate conține payout_id)
        
    Returns:
        Dict cu payout_id, dates etc.
    """
    info = {
        'payout_id': None,
        'payout_date': None,
        'reference_period_start': None,
        'reference_period_end': None
    }
    
    # Încearcă să extragi payout_id din nume fișier
    # ex: payout_notice_36898183_2026_4100309867.pdf
    filename_match = re.search(r'_(\d{8,})\.pdf', filename)
    if filename_match:
        info['payout_id'] = int(filename_match.group(1))
    
    # Sau din text
    payout_id_match = re.search(r'Payout\s+ID[:\s]+(\d+)', text, re.IGNORECASE)
    if payout_id_match:
        info['payout_id'] = int(payout_id_match.group(1))
    
    # Încearcă să extragi date
    date_patterns = [
        r'Data\s+platii?[:\s]+(\d{2}[-/.]\d{2}[-/.]\d{4})',
        r'Payout\s+date[:\s]+(\d{2}[-/.]\d{2}[-/.]\d{4})',
        r'(\d{2}[-/.]\d{2}[-/.]\d{4})',  # orice dată
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            date_str = match.group(1)
            # Încearcă să parsezi data
            for date_format in ['%d-%m-%Y', '%d.%m.%Y', '%d/%m/%Y']:
                try:
                    info['payout_date'] = datetime.strptime(date_str, date_format).date()
                    break
                except ValueError:
                    continue
            if info['payout_date']:
                break
    
    # Perioada de referință (dacă există)
    period_match = re.search(
        r'Perioada[:\s]+(\d{2}[-/.]\d{2}[-/.]\d{4})\s*[-–]\s*(\d{2}[-/.]\d{2}[-/.]\d{4})',
        text,
        re.IGNORECASE
    )
    if period_match:
        for date_format in ['%d-%m-%Y', '%d.%m.%Y', '%d/%m/%Y']:
            try:
                info['reference_period_start'] = datetime.strptime(
                    period_match.group(1), date_format
                ).date()
                info['reference_period_end'] = datetime.strptime(
                    period_match.group(2), date_format
                ).date()
                break
            except ValueError:
                continue
    
    return info


# ═══════════════════════════════════════════════════════
# CALCUL HASH PDF
# ═══════════════════════════════════════════════════════

def calculate_pdf_hash(pdf_bytes: bytes) -> str:
    """Calculează SHA256 hash pentru PDF."""
    return hashlib.sha256(pdf_bytes).hexdigest()


# ═══════════════════════════════════════════════════════
# SALVARE ÎN DB
# ═══════════════════════════════════════════════════════

def save_payout_to_db(
    conn,
    payout_info: Dict,
    total_amount: float,
    invoices: List[Dict],
    pdf_filename: str,
    pdf_hash: str,
    pages_count: int,
    uploaded_by: str = None
) -> int:
    """
    Salvează payout în DB.
    
    Returns:
        int: payout_header_id (id-ul rândului inserat)
    """
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    
    try:
        # 1. Verifică dacă PDF-ul există deja (prin hash)
        cursor.execute("""
            SELECT id FROM emag_payout_header
            WHERE pdf_file_hash = %s
        """, (pdf_hash,))
        
        existing = cursor.fetchone()
        if existing:
            raise ValueError(
                f"PDF-ul a fost deja încărcat! "
                f"(payout_header_id={existing['id']})"
            )
        
        # 2. Insert în emag_payout_header
        cursor.execute("""
            INSERT INTO emag_payout_header (
                payout_id,
                payout_date,
                reference_period_start,
                reference_period_end,
                total_amount_pdf,
                currency,
                pdf_filename,
                pdf_file_hash,
                pages_count,
                uploaded_by
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            RETURNING id
        """, (
            payout_info.get('payout_id'),
            payout_info.get('payout_date'),
            payout_info.get('reference_period_start'),
            payout_info.get('reference_period_end'),
            total_amount,
            'RON',
            pdf_filename,
            pdf_hash,
            pages_count,
            uploaded_by
        ))
        
        payout_header_id = cursor.fetchone()['id']
        
        # 3. Insert în emag_payout_invoices
        for invoice in invoices:
            cursor.execute("""
                INSERT INTO emag_payout_invoices (
                    payout_header_id,
                    invoice_number,
                    invoice_type,
                    invoice_amount_pdf,
                    position_in_pdf,
                    raw_line
                ) VALUES (
                    %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (payout_header_id, invoice_number)
                DO NOTHING
            """, (
                payout_header_id,
                invoice['invoice_number'],
                invoice['invoice_type'],
                invoice.get('invoice_amount'),
                invoice['position_in_pdf'],
                invoice['raw_line']
            ))
        
        conn.commit()
        return payout_header_id
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        cursor.close()


# ═══════════════════════════════════════════════════════
# FUNCȚIE PRINCIPALĂ
# ═══════════════════════════════════════════════════════

def parse_payout_pdf(
    pdf_bytes: bytes,
    filename: str,
    conn = None,
    uploaded_by: str = None
) -> Dict:
    """
    Parser principal pentru PDF payout.
    
    Args:
        pdf_bytes: Bytes-urile fișierului PDF
        filename: Numele fișierului
        conn: Conexiune psycopg2 (opțional, pentru salvare în DB)
        uploaded_by: Username-ul celui care a uploadat
        
    Returns:
        Dict cu rezultatele parsării
    """
    # 1. Calcul hash
    pdf_hash = calculate_pdf_hash(pdf_bytes)
    
    # 2. Extrage text
    text = extract_text_from_pdf(pdf_bytes)
    
    # 3. Extrage informații
    payout_info = extract_payout_info(text, filename)
    total_amount = extract_total_amount(text)
    invoices = extract_invoices(text)
    
    # 4. Număr pagini
    with pdfplumber.open(pdf_bytes) as pdf:
        pages_count = len(pdf.pages)
    
    result = {
        'pdf_hash': pdf_hash,
        'filename': filename,
        'pages_count': pages_count,
        'payout_info': payout_info,
        'total_amount': total_amount,
        'invoices': invoices,
        'invoices_count': len(invoices),
        'payout_header_id': None
    }
    
    # 5. Salvează în DB (dacă este furnizată conexiune)
    if conn:
        payout_header_id = save_payout_to_db(
            conn=conn,
            payout_info=payout_info,
            total_amount=total_amount,
            invoices=invoices,
            pdf_filename=filename,
            pdf_hash=pdf_hash,
            pages_count=pages_count,
            uploaded_by=uploaded_by
        )
        result['payout_header_id'] = payout_header_id
    
    return result


# ═══════════════════════════════════════════════════════
# TESTARE
# ═══════════════════════════════════════════════════════

if __name__ == "__main__":
    # Test cu PDF-ul tău
    test_file = 'payout_notice_36898183_2026_4100309867.pdf'
    
    try:
        with open(test_file, 'rb') as f:
            pdf_bytes = f.read()
        
        result = parse_payout_pdf(
            pdf_bytes=pdf_bytes,
            filename=test_file,
            conn=None  # Nu salvăm în DB la test
        )
        
        print(f"\n{'='*60}")
        print(f"✅ PDF: {result['filename']}")
        print(f"{'='*60}")
        print(f"Hash: {result['pdf_hash'][:16]}...")
        print(f"Pagini: {result['pages_count']}")
        print(f"\n📊 INFORMAȚII PAYOUT:")
        print(f"  Payout ID: {result['payout_info']['payout_id']}")
        print(f"  Payout Date: {result['payout_info']['payout_date']}")
        print(f"  Perioadă: {result['payout_info']['reference_period_start']} → {result['payout_info']['reference_period_end']}")
        print(f"\n💰 TOTAL AMOUNT: {result['total_amount']:,.2f} RON")
        print(f"\n📄 FACTURI GĂSITE: {result['invoices_count']}")
        
        if result['invoices']:
            print("\nPrimele 5 facturi:")
            for inv in result['invoices'][:5]:
                amount_str = f"{inv['invoice_amount']:,.2f} RON" if inv['invoice_amount'] else "N/A"
                print(f"  • {inv['invoice_number']} ({inv['invoice_type']}) - {amount_str}")
        
    except FileNotFoundError:
        print(f"❌ Fișierul {test_file} nu a fost găsit!")
    except Exception as e:
        print(f"❌ Eroare: {e}")
        import traceback
        traceback.print_exc()
