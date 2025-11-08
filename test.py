# -*- coding: utf-8 -*-
"""
Buy Box Analyzer - 100% alinhado com PriceTrack
- Normaliza seller (ex: Amobeleza = amobeleza = AMOBELEZA)
- Compara PREÇO POR EAN + MARKETPLACE
- Mostra vencedor, preço e diferença
- Exporta CSV
"""

import requests
import difflib
import re
import csv
from typing import Dict, List, Any
from collections import defaultdict
import argparse
import sys

def normalize_seller_name(name: str) -> str:
    """Remove acentos, espaços, pontuação e deixa minúsculo."""
    name = re.sub(r'[àáâãäå]', 'a', name)
    name = re.sub(r'[èéêë]', 'e', name)
    name = re.sub(r'[ìíîï]', 'i', name)
    name = re.sub(r'[òóôõö]', 'o', name)
    name = re.sub(r'[ùúûü]', 'u', name)
    name = re.sub(r'[ç]', 'c', name)
    return re.sub(r'[^a-z0-9]', '', name.lower())

def agrupar_sellers(data: List[Dict]) -> Dict[str, Dict]:
    """Agrupa itens por seller normalizado."""
    agrupados = {}
    for item in data:
        loja = item.get('loja')
        if not loja:
            continue
        norm = normalize_seller_name(loja)
        if norm not in agrupados:
            agrupados[norm] = {
                'nome_exibicao': loja,
                'marketplaces': set(),
                'itens': []
            }
        grupo = agrupados[norm]
        if item.get('marketplace'):
            grupo['marketplaces'].add(item['marketplace'])
        grupo['itens'].append(item)
    return agrupados

def get_seller_buyboxes(seller_input: str, export_csv: str = None) -> Dict[str, Any]:
    url = 'https://pricetrack-api.onrender.com/api/products/'
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        data = [item for item in data if item.get('status') == 'ativo']
    except Exception as e:
        raise ValueError(f'Erro ao acessar API: {e}')

    # === 1. Agrupa sellers por nome normalizado ===
    sellers_agrupados = agrupar_sellers(data)
    norm_input = normalize_seller_name(seller_input)

    # === 2. Busca seller (exato ou sugestão) ===
    if norm_input in sellers_agrupados:
        grupo = sellers_agrupados[norm_input]
        selected_seller = grupo['nome_exibicao']
        seller_items = grupo['itens']
        marketplaces = grupo['marketplaces']
    else:
        close = difflib.get_close_matches(norm_input, sellers_agrupados.keys(), n=5, cutoff=0.6)
        if not close:
            raise ValueError(f'Nenhum seller próximo a "{seller_input}".')
        print('Opções próximas:')
        for i, k in enumerate(close):
            print(f'{i}: {sellers_agrupados[k]["nome_exibicao"]} '
                  f'({len(sellers_agrupados[k]["marketplaces"])} marketplaces)')
        try:
            idx = int(input('Selecione o índice: '))
            k = close[idx]
            grupo = sellers_agrupados[k]
            selected_seller = grupo['nome_exibicao']
            seller_items = grupo['itens']
            marketplaces = grupo['marketplaces']
        except:
            raise ValueError('Seleção inválida.')

    print(f'\nSeller: {selected_seller}')
    print(f'Encontrado em {len(marketplaces)} marketplace(s): {", ".join(sorted(marketplaces))}\n')

    # Filtra itens com preço válido
    seller_items = [
        i for i in seller_items
        if i.get('preco_final') and i['preco_final'] != '0.00'
    ]

    if not seller_items:
        result = {
            'seller_name': selected_seller,
            'marketplaces_count': len(marketplaces),
            'marketplaces': sorted(marketplaces),
            'quantidade_ganhos': 0,
            'produtos_ganhos': [],
            'quantidade_perdidos': 0,
            'produtos_perdidos': []
        }
        if export_csv:
            salvar_csv(result, export_csv)
        return result

    # === 3. Mapeia preços por (EAN, marketplace) + seller normalizado ===
    price_map = defaultdict(dict)  # (ean, mp) → {norm_seller: preço}
    item_lookup = {}               # (ean, mp) → item com URL/descrição

    for item in data:
        preco = item.get('preco_final')
        if not preco or preco == '0.00':
            continue
        try:
            p = float(preco)
        except:
            continue
        ean = item['ean']
        mp = item['marketplace']
        loja = item['loja']
        key = (ean, mp)
        norm_loja = normalize_seller_name(loja)

        price_map[key][norm_loja] = p
        item_lookup[key] = item  # último item (qualquer um com URL)

    # === 4. Calcula menor preço por (EAN, marketplace) ===
    min_prices = {}
    for key, sellers in price_map.items():
        min_prices[key] = min(sellers.values())

    # === 5. Classifica produtos do seller ===
    won = []
    lost = []
    norm_target = norm_input

    for item in seller_items:
        key = (item['ean'], item['marketplace'])
        if key not in min_prices:
            continue
        try:
            meu_preco = float(item['preco_final'])
        except:
            continue

        min_preco = min_prices[key]
        vencedor_norm = [s for s, p in price_map[key].items() if abs(p - min_preco) < 0.01]
        is_win = norm_target in vencedor_norm

        # Busca nome real do vencedor
        vencedor_real = []
        for v_norm in vencedor_norm:
            for it in data:
                if (it['ean'] == key[0] and
                    it['marketplace'] == key[1] and
                    normalize_seller_name(it['loja']) == v_norm):
                    vencedor_real.append(it['loja'])
                    break
            if not vencedor_real:
                vencedor_real.append(v_norm.upper())

        info = {
            'descricao': item.get('descricao', 'Sem descrição')[:60],
            'ean': item['ean'],
            'marketplace': item['marketplace'],
            'preco': meu_preco,
            'url': item.get('url', 'URL não disponível'),
            'min_preco': min_preco,
            'vencedor': ' | '.join(vencedor_real) if vencedor_real else 'Desconhecido',
            'diferenca': round(meu_preco - min_preco, 2)
        }

        if is_win:
            won.append(info)
        else:
            lost.append(info)

    result = {
        'seller_name': selected_seller,
        'marketplaces_count': len(marketplaces),
        'marketplaces': sorted(marketplaces),
        'quantidade_ganhos': len(won),
        'produtos_ganhos': won,
        'quantidade_perdidos': len(lost),
        'produtos_perdidos': lost
    }

    if export_csv:
        salvar_csv(result, export_csv)

    return result

def salvar_csv(result: Dict, caminho: str):
    with open(caminho, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Status', 'Produto', 'EAN', 'Marketplace', 'Seu Preço', 'Menor Preço', 'Vencedor', 'Diferença', 'URL'])
        for p in result['produtos_ganhos']:
            writer.writerow([
                'GANHO',
                p['descricao'],
                p['ean'],
                p['marketplace'],
                f"R${p['preco']:.2f}",
                f"R${p['min_preco']:.2f}",
                p['vencedor'],
                f"Ganhando por R${-p['diferenca']:.2f}" if p['diferenca'] < 0 else "Empate",
                p['url']
            ])
        for p in result['produtos_perdidos']:
            writer.writerow([
                'PERDA',
                p['descricao'],
                p['ean'],
                p['marketplace'],
                f"R${p['preco']:.2f}",
                f"R${p['min_preco']:.2f}",
                p['vencedor'],
                f"Perdendo por R${p['diferenca']:.2f}",
                p['url']
            ])
    print(f"CSV salvo em: {caminho}")

def imprimir_resultado(result: Dict):
    print("="*80)
    print(f"RESULTADO PARA: {result['seller_name']}")
    print(f"Presente em {result['marketplaces_count']} marketplace(s): {', '.join(result['marketplaces'])}")
    print("="*80)

    def print_lista(titulo, lista):
        print(f"\n{titulo}: {len(lista)} produto(s)")
        print("-" * 90)
        for i, p in enumerate(lista, 1):
            dif = f"por R${-p['diferenca']:.2f}" if p['diferenca'] < 0 else f"por R${p['diferenca']:.2f}"
            status = "GANHANDO" if 'GANHO' in titulo else "PERDENDO"
            print(f"{i}. {p['descricao']:<60} | EAN: {p['ean']}")
            print(f"   Marketplace: {p['marketplace']} | Preço: R${p['preco']:.2f}")
            print(f"   Vencedor: {p['vencedor']} (R${p['min_preco']:.2f}) → {status} {dif}")
            print(f"   URL: {p['url']}\n")

    print_lista("GANHOS", result['produtos_ganhos'])
    print_lista("PERDIDOS", result['produtos_perdidos'])

    if not result['produtos_ganhos'] and not result['produtos_perdidos']:
        print("Nenhum produto com preço válido.")

# ====================== EXECUÇÃO ======================
def main():
    parser = argparse.ArgumentParser(description="Buy Box Analyzer - 100% alinhado com PriceTrack")
    parser.add_argument('seller', nargs='?', help='Nome do seller')
    parser.add_argument('--csv', help='Exportar para CSV')
    args = parser.parse_args()

    if not args.seller:
        print("Buy Box Analyzer - 100% alinhado com PriceTrack\n")
        args.seller = input("Digite o nome do seller: ").strip()

    try:
        result = get_seller_buyboxes(args.seller, export_csv=args.csv)
        if not args.csv:
            imprimir_resultado(result)
    except Exception as e:
        print(f"Erro: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()