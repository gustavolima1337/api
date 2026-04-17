import logging
from ninja import NinjaAPI, ModelSchema, Schema
from typing import List, Optional, Any, Dict
from .models import ProductURL, ProductDetails, PriceChange
from ninja.errors import ValidationError
from django.db import IntegrityError
from decimal import Decimal
from datetime import datetime
from django.utils import timezone
from django.shortcuts import get_object_or_404
from collections import defaultdict
from django.db.models import Q
import re


# Configura o logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

api = NinjaAPI(
    title="PriceTrack - Buy Box Analyzer",
    version="1.0.0",
    description="Monitoramento de produtos"
)

# =================== SCHEMAS ===================

class ProductDetailsOut(Schema):
    ean: str
    sku: str
    loja: str
    preco_final: Decimal
    data_hora: datetime
    marketplace: str
    change_price: int
    key_loja: str
    key_sku: str
    descricao: str
    review: float
    imagem: str
    status: str
    preco_pricing: Optional[Decimal]
    preco_buybox: Optional[Decimal]
    url: str
    marca: str
    categoria: str
    loja_normalizada: str

class ProdutoBuyboxSchema(Schema):
    ean: str
    descricao: str
    url: str
    marketplace: str
    preco_final: float
    menor_preco: Optional[float] = None
    vencedor: Optional[str] = None
    diferenca: Optional[float] = None

class BuyboxSellerResponse(Schema):
    seller: str
    marketplaces: List[str]
    ganhos: int
    perdas: int
    resumo_por_marketplace: Dict[str, Dict[str, int]]  # 👈 adicione isso
    produtos_ganhando: List[ProdutoBuyboxSchema]
    produtos_perdendo: List[ProdutoBuyboxSchema]
    
class ErrorResponse(Schema):
    detail: str
    data: Optional[dict] = None

class ProductsDetailsIn(ModelSchema):
    class Config:
        model = ProductDetails
        model_fields = [
            'ean', 'sku', 'key_sku', 'loja', 'preco_final', 'data_hora', 'marketplace',
            'key_loja', 'descricao', 'review', 'imagem', 'status',
            'preco_pricing', 'preco_buybox', 'url', 'marca','categoria'
        ]

class PriceChangeOut(Schema):
    ean: str
    loja: str
    key_loja: str
    preco_final_antigo: Optional[Decimal]
    preco_final_novo: Decimal
    timestamp: datetime
    url: str
    descricao: str

class ProdutoBuyboxOut(Schema):
    ean: str
    sku: str
    url: str
    descricao: str
    preco_final: float
    marketplace: str

class SellerBuyboxOut(Schema):
    loja: str
    key_loja: str
    total_buyboxes: int
    marketplaces: List[str]
    total_produtos: int
    produtos: List[ProdutoBuyboxOut]

class EstatisticaSellerOut(Schema):
    loja: str
    total_buyboxes: int
    marketplaces: List[str]
    total_produtos: int
    produtos: List[ProdutoBuyboxOut]

class BuyboxAnalysisOut(Schema):
    seller_mais_buyboxes: Optional[SellerBuyboxOut]
    total_buyboxes_analisados: int
    estatisticas_completas: dict
    mensagem: Optional[str] = None

class ProductURLInputSchema(Schema):
    ean_key: str
    ean: str
    brand: str
    url: str
    client_name: str
    is_active: Optional[bool] = True

    @staticmethod
    def validate_ean(ean: str) -> str:
        if not (ean.isdigit() and len(ean) == 13):
            raise ValidationError("EAN must be a 13-digit number")
        return ean

    @staticmethod
    def validate_url(url: str) -> str:
        if not url.startswith(('http://', 'https://')):
            raise ValidationError("URL must start with http:// or https://")
        return url

    @staticmethod
    def validate_brand(brand: str) -> str:
        if not brand or brand.strip() == "":
            raise ValidationError("Brand cannot be empty")
        return brand

class UpdateIsActiveSchema(Schema):
    ean_key: str
    is_active: bool

class SchemaProductURL(ModelSchema):
    class Config:
        model = ProductURL
        model_fields = ['ean_key', 'ean', 'brand', 'url', 'client', 'created_at', 'client_name', 'is_active']

class UpdatePrecosSchema(Schema):
    key_sku: str
    preco_pricing: Optional[Decimal] = None
    preco_buybox: Optional[Decimal] = None
    
    def validate(self):
        """Valida que pelo menos um preço foi fornecido"""
        if self.preco_pricing is None and self.preco_buybox is None:
            raise ValidationError("Informe pelo menos preco_pricing ou preco_buybox")
        
        if self.preco_pricing is not None and self.preco_pricing < 0:
            raise ValidationError("preco_pricing não pode ser negativo")
        
        if self.preco_buybox is not None and self.preco_buybox < 0:
            raise ValidationError("preco_buybox não pode ser negativo")
        
        return self

class LojaOut(Schema):
    loja_normalizada: str

class ProdutosMonitorados(Schema):
    ean: str
    descricao: str

# =================== HELPERS ===================

def normalizar_loja(nome: str) -> str:
    if not nome:
        return "sem_loja"
    nome = re.sub(r'[àáâãäå]', 'a', nome)
    nome = re.sub(r'[èéêë]', 'e', nome)
    nome = re.sub(r'[ìíîï]', 'i', nome)
    nome = re.sub(r'[òóôõö]', 'o', nome)
    nome = re.sub(r'[ùúûü]', 'u', nome)
    nome = re.sub(r'[ç]', 'c', nome)
    return re.sub(r'[^a-z0-9]', '', nome.lower())

# =================== ENDPOINT NINJA ===================

def get_buyboxes_by_seller(seller_name: str) -> Dict:
    """
    Calcula Buy Box para um seller usando campo `loja_normalizada`.
    Mostra marketplaces onde está ganhando/perdendo e a diferença
    para o segundo lugar quando está ganhando.
    """

    # 1️⃣ Busca produtos ATIVOS do seller
    seller_products = list(
        ProductDetails.objects.filter(
            status="ativo",
            loja_normalizada=seller_name
        ).select_related()
    )

    if not seller_products:
        return {
            "seller": seller_name,
            "mensagem": "Seller não encontrado ou sem produtos ativos",
            "marketplaces": [],
            "marketplaces_ganhando": [],
            "marketplaces_perdendo": [],
            "ganhos": 0,
            "perdas": 0,
            "resumo_por_marketplace": {},
            "produtos_ganhando": [],
            "produtos_perdendo": []
        }

    # 2️⃣ Coleta EANs
    eans = {p.ean for p in seller_products}

    # 3️⃣ Busca todos os produtos ativos desses EANs
    all_products = list(
        ProductDetails.objects.filter(
            status="ativo",
            ean__in=eans
        )
    )

    # 4️⃣ Agrupa por (EAN, marketplace)
    grupos = defaultdict(list)
    for p in all_products:
        key = (p.ean, p.marketplace)
        grupos[key].append({
            'loja': p.loja,
            'loja_normalizada': p.loja_normalizada,
            'preco_final': float(p.preco_final),
            'preco_buybox': float(p.preco_buybox) if p.preco_buybox else 0.0,
            'url': p.url,
            'descricao': p.descricao,
            'marketplace': p.marketplace,
            'ean': p.ean
        })

    # 5️⃣ Analisa cada grupo
    ganhando = []
    perdendo = []
    marketplaces = set()
    marketplaces_ganhando = set()
    marketplaces_perdendo = set()
    resumo_por_marketplace = defaultdict(lambda: {"ganhos": 0, "perdas": 0})

    for (ean, marketplace), itens in grupos.items():
        marketplaces.add(marketplace)
        validos = [i for i in itens if i['preco_final'] > 0]
        if not validos:
            continue

        # Ordena por preço crescente
        validos_ordenados = sorted(validos, key=lambda x: x['preco_final'])

        vencedor = validos_ordenados[0]
        min_preco = vencedor['preco_final']

        # Segundo menor preço
        segundo = validos_ordenados[1] if len(validos_ordenados) > 1 else None
        segundo_preco = segundo['preco_final'] if segundo else None

        meus_itens = [i for i in validos if i['loja_normalizada'] == seller_name]
        for meu in meus_itens:
            info_base = {
                "ean": ean,
                "descricao": meu['descricao'][:100],
                "url": meu['url'],
                "marketplace": marketplace,
                "preco_final": meu['preco_final']
            }

            # 🟢 Ganhando a BuyBox
            if abs(meu['preco_final'] - min_preco) < 0.01:
                diferenca_segundo = None
                vantagem_percentual = None
                if segundo_preco:
                    diferenca_segundo = round(segundo_preco - meu['preco_final'], 2)
                    vantagem_percentual = round((diferenca_segundo / segundo_preco) * 100, 2)

                ganhando.append({
                    **info_base,
                    "menor_preco": segundo_preco,
                    "vencedor": meu['loja'],
                    "diferenca": diferenca_segundo,
                    "vantagem_percentual": vantagem_percentual
                })
                marketplaces_ganhando.add(marketplace)
                resumo_por_marketplace[marketplace]["ganhos"] += 1

            # 🔴 Perdendo a BuyBox
            else:
                diferenca = round(meu['preco_final'] - min_preco, 2)
                perdendo.append({
                    **info_base,
                    "menor_preco": min_preco,
                    "vencedor": vencedor['loja'],
                    "diferenca": diferenca
                })
                marketplaces_perdendo.add(marketplace)
                resumo_por_marketplace[marketplace]["perdas"] += 1

    # 6️⃣ Monta resposta
    return {
        "seller": seller_name,
        "marketplaces": sorted(marketplaces),
        "marketplaces_ganhando": sorted(marketplaces_ganhando),
        "marketplaces_perdendo": sorted(marketplaces_perdendo),
        "ganhos": len(ganhando),
        "perdas": len(perdendo),
        "resumo_por_marketplace": dict(resumo_por_marketplace),
        "produtos_ganhando": ganhando,
        "produtos_perdendo": perdendo
    }

@api.get("/products/monitorados", response=list[dict])
def get_monitored_products(request):
    """
    Retorna lista de produtos monitorados (únicos por EAN),
    apenas com EAN e descrição.
    Deduplicação feita no banco via distinct() para melhor performance.
    """
    from django.db.models import Min

    resultado = (
        ProductDetails.objects
        .filter(status="ativo")
        .values("ean")
        .annotate(descricao=Min("descricao"))  # pega uma descrição por EAN no banco
        .order_by("descricao")
    )

    logger.info("Retornados %d produtos únicos por EAN.", len(resultado))

    return list(resultado)

@api.get("/products/{ean}/melhor_oferta", response=Dict[str, Any])
def get_lowest_price_by_marketplace(request, ean: str):
    """
    Retorna o seller com o menor preço para o produto (EAN)
    em cada marketplace onde ele está sendo vendido.
    """

    # validação simples do EAN
    if not ean.isdigit() or len(ean) != 13:
        return 422, {"detail": "EAN deve conter 13 dígitos numéricos."}

    produtos = ProductDetails.objects.filter(
        ean=ean,
        status="ativo"
    )

    if not produtos.exists():
        return 404, {"detail": f"Nenhum produto ativo encontrado para o EAN {ean}"}

    # Agrupamento por marketplace
    marketplaces = {}
    for produto in produtos:
        marketplace = produto.marketplace
        preco = float(produto.preco_final or 0)

        # se for o primeiro produto do marketplace ou preço menor -> atualiza
        if marketplace not in marketplaces or preco < marketplaces[marketplace]["preco_final"]:
            marketplaces[marketplace] = {
                "ean": produto.ean,
                "descricao": produto.descricao,
                "marca": produto.marca,
                "marketplace": produto.marketplace,
                "seller": produto.loja,
                "loja_normalizada": produto.loja_normalizada,
                "preco_final": preco,
                "url": produto.url if hasattr(produto, "url") else None,
                "imagem": produto.imagem,
            }

    # cálculos agregados gerais
    precos = [v["preco_final"] for v in marketplaces.values()]
    menor_preco = min(precos)
    maior_preco = max(precos)
    media_preco = round(sum(precos) / len(precos), 2)

    logger.info(f"Analisados {len(produtos)} produtos para EAN {ean}")

    return {
        "ean": ean,
        "marca": produtos.first().marca,
        "descricao": produtos.first().descricao,
        "quantidade_marketplaces": len(marketplaces),
        "resumo_precos": {
            "menor_preco_geral": menor_preco,
            "maior_preco_geral": maior_preco,
            "media_precos": media_preco
        },
        "melhores_ofertas": list(marketplaces.values())
    }

@api.get("/buyboxes/{seller_name}",
    response={200: BuyboxSellerResponse, 404: ErrorResponse}
)
def api_get_buyboxes(request, seller_name: str):
    """
    Retorna Buy Box por seller (usando loja_normalizada).
    Ex: /api/buyboxes/seller/hairpro
    """
    try:
        resultado = get_buyboxes_by_seller(seller_name)

        if "mensagem" in resultado:
            return 404, {"detail": resultado["mensagem"]}

        return 200, resultado

    except Exception as e:
        logger.error(f"Erro no endpoint: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return 404, {"detail": "Erro interno no servidor"}

def identificar_seller_mais_buyboxes(produtos=None, usar_banco=True):
    """
    Identifica o seller (loja) que mais está ganhando buyboxes.
    
    Args:
        produtos: Lista opcional de produtos (ProductDetails ou dicts)
        usar_banco: Se True, busca produtos do banco. Se False, usa a lista fornecida
    
    Returns:
        dict com informações sobre o seller com mais buyboxes e estatísticas
    """
    if usar_banco:
        # Busca produtos ativos do banco
        produtos_queryset = ProductDetails.objects.filter(status="ativo")
        produtos = list(produtos_queryset)
        logger.info(f"Analisando {len(produtos)} produtos do banco de dados")
    else:
        if not produtos:
            return {
                "erro": "Lista de produtos vazia",
                "seller_mais_buyboxes": None,
                "total_buyboxes": 0,
                "estatisticas": {}
            }
        logger.info(f"Analisando {len(produtos)} produtos fornecidos")
    
    # Agrupa produtos por EAN
    produtos_por_ean = defaultdict(list)
    
    for produto in produtos:
        # Se for um objeto do modelo, converte para dict
        if hasattr(produto, 'ean'):
            ean = produto.ean
            loja = produto.loja
            key_loja = produto.key_loja
            preco_final = float(produto.preco_final)
            preco_buybox = float(produto.preco_buybox) if produto.preco_buybox else 0.0
            marketplace = produto.marketplace
            status = produto.status
            url = produto.url
            descricao = produto.descricao
            sku = produto.sku
        else:
            # Se for dict
            ean = produto.get('ean')
            loja = produto.get('loja')
            key_loja = produto.get('key_loja')
            preco_final = float(produto.get('preco_final', 0))
            preco_buybox_str = produto.get('preco_buybox')
            if preco_buybox_str:
                preco_buybox = float(preco_buybox_str) if preco_buybox_str != "0.00" else 0.0
            else:
                preco_buybox = 0.0
            marketplace = produto.get('marketplace')
            status = produto.get('status', 'ativo')
            url = produto.get('url', '')
            descricao = produto.get('descricao', '')
            sku = produto.get('sku', '')
        
        # Considera apenas produtos ativos
        if status != 'ativo':
            continue
            
        produtos_por_ean[ean].append({
            'loja': loja,
            'key_loja': key_loja,
            'preco_final': preco_final,
            'preco_buybox': preco_buybox,
            'marketplace': marketplace,
            'ean': ean,
            'url': url,
            'descricao': descricao,
            'sku': sku
        })
    
    # Contador de buyboxes por seller
    buyboxes_por_seller = defaultdict(lambda: {
        'total_buyboxes': 0,
        'loja': '',
        'key_loja': '',
        'marketplaces': set(),
        'produtos': []  # Lista de produtos com informações detalhadas
    })
    
    # Para cada EAN, identifica o seller que ganhou o buybox
    for ean, produtos_ean in produtos_por_ean.items():
        buybox_winner = None
        menor_preco = float('inf')
        
        # Verifica se algum produto tem preco_buybox > 0 (marcado explicitamente)
        produtos_com_buybox = [p for p in produtos_ean if p['preco_buybox'] > 0]
        
        if produtos_com_buybox:
            # Se há produtos com preco_buybox > 0, o buybox winner é aquele
            # Pega o produto com menor preco_final entre os que têm buybox
            buybox_winner = min(produtos_com_buybox, key=lambda x: x['preco_final'])
        else:
            # Caso contrário, o buybox winner é o seller com menor preço
            if produtos_ean:
                buybox_winner = min(produtos_ean, key=lambda x: x['preco_final'])
        
        if buybox_winner:
            loja = buybox_winner['loja']
            key_loja = buybox_winner['key_loja']
            marketplace = buybox_winner['marketplace']
            
            # Atualiza estatísticas do seller
            if buyboxes_por_seller[key_loja]['loja'] == '':
                buyboxes_por_seller[key_loja]['loja'] = loja
                buyboxes_por_seller[key_loja]['key_loja'] = key_loja
            
            buyboxes_por_seller[key_loja]['total_buyboxes'] += 1
            buyboxes_por_seller[key_loja]['marketplaces'].add(marketplace)
            
            # Adiciona informações detalhadas do produto
            buyboxes_por_seller[key_loja]['produtos'].append({
                'ean': ean,
                'sku': buybox_winner.get('sku', ''),
                'url': buybox_winner.get('url', ''),
                'descricao': buybox_winner.get('descricao', ''),
                'preco_final': buybox_winner['preco_final'],
                'marketplace': marketplace
            })
    
    if not buyboxes_por_seller:
        return {
            "seller_mais_buyboxes": None,
            "total_buyboxes": 0,
            "estatisticas": {},
            "mensagem": "Nenhum buybox encontrado"
        }
    
    # Encontra o seller com mais buyboxes
    seller_mais_buyboxes_key = max(buyboxes_por_seller.keys(), 
                                    key=lambda k: buyboxes_por_seller[k]['total_buyboxes'])
    
    seller_info = buyboxes_por_seller[seller_mais_buyboxes_key]
    
    # Prepara resultado
    resultado = {
        "seller_mais_buyboxes": {
            "loja": seller_info['loja'],
            "key_loja": seller_info['key_loja'],
            "total_buyboxes": seller_info['total_buyboxes'],
            "marketplaces": list(seller_info['marketplaces']),
            "total_produtos": len(seller_info['produtos']),
            "produtos": seller_info['produtos']
        },
        "total_buyboxes_analisados": sum(stats['total_buyboxes'] for stats in buyboxes_por_seller.values()),
        "estatisticas_completas": {
            key: {
                "loja": stats['loja'],
                "total_buyboxes": stats['total_buyboxes'],
                "marketplaces": list(stats['marketplaces']),
                "total_produtos": len(stats['produtos']),
                "produtos": stats['produtos']
            }
            for key, stats in buyboxes_por_seller.items()
        }
    }
    
    logger.info(f"Seller com mais buyboxes: {seller_info['loja']} com {seller_info['total_buyboxes']} buyboxes")
    
    return resultado

@api.get("/products/lojas", response=List[LojaOut])
def listar_lojas_normalizadas(request):
    """
    Retorna apenas os valores únicos de loja_normalizada dos produtos.
    """
    lojas = (
        ProductDetails.objects
        .values_list("loja_normalizada", flat=True)
        .distinct()
    )
    return [{"loja_normalizada": loja} for loja in lojas]

@api.get("/buyboxes/", response={200: Dict[str, Any], 404: ErrorResponse})
def get_top_seller_buyboxes(request):
    """
    Retorna o seller (loja) que mais está ganhando buyboxes.
    
    Analisa todos os produtos ativos no banco de dados e identifica qual seller
    está ganhando mais buyboxes baseado no menor preço por EAN.
    
    Returns:
        - seller_mais_buyboxes: Informações do seller com mais buyboxes
        - total_buyboxes_analisados: Total de buyboxes analisados
        - estatisticas_completas: Estatísticas de todos os sellers
    """
    try:
        logger.info("Buscando seller com mais buyboxes...")
        
        # Chama a função que analisa os produtos do banco
        resultado = identificar_seller_mais_buyboxes(usar_banco=True)
        
        # Verifica se há erro
        if resultado.get("erro"):
            logger.warning(f"Erro ao analisar buyboxes: {resultado.get('erro')}")
            return 404, {"detail": resultado.get("erro")}
        
        # Verifica se não há buyboxes
        if resultado.get("mensagem") and not resultado.get("seller_mais_buyboxes"):
            logger.info(resultado.get("mensagem"))
            return 200, {
                "seller_mais_buyboxes": None,
                "total_buyboxes_analisados": 0,
                "estatisticas_completas": {},
                "mensagem": resultado.get("mensagem")
            }
        
        seller_info = resultado.get("seller_mais_buyboxes")
        
        logger.info(f"Retornando análise de buyboxes - Seller: {seller_info['loja'] if seller_info else 'N/A'}, Total: {resultado.get('total_buyboxes_analisados', 0)}")
        
        # Retorna o resultado diretamente (já está no formato JSON compatível)
        return 200, resultado
        
    except Exception as e:
        logger.error(f"Erro ao buscar seller com mais buyboxes: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return 404, {"detail": f"Erro ao analisar buyboxes: {str(e)}"}

@api.get('urls/', response=List[SchemaProductURL])
def get_urls(request):
    return ProductURL.objects.all()

@api.get('products/', response=List[ProductDetailsOut])
def get_products(request, limit: int = 100, offset: int = 0):
    return ProductDetails.objects.all().order_by('-data_hora')[offset:offset + limit]

@api.get("/products/{ean}", response=List[ProductDetailsOut])
def get_product_by_ean(request, ean: str):
    """
    Busca APENAS produtos ATIVOS de um ÚNICO EAN.
    
    Exemplo: /api/products/by_ean/7891234567890
    """
    # Validação rigorosa do EAN
    if not ean.isdigit() or len(ean) != 13:
        logger.warning(f"EAN inválido recebido: {ean}")
        return 422, {"detail": "EAN deve ter exatamente 13 dígitos numéricos"}

    logger.info(f"Consulta de produtos ATIVOS para o EAN: {ean}")

    # ✅ FILTRA APENAS PRODUTOS COM status="ativo"
    queryset = ProductDetails.objects.filter(
        ean=ean,
        status="ativo"  # <--- AQUI O FILTRO PRINCIPAL
    ).order_by('-data_hora')

    if not queryset.exists():
        logger.info(f"Nenhum produto ATIVO encontrado para o EAN: {ean}")
        return 404, {"detail": f"Nenhum produto ativo encontrado para o EAN {ean}"}

    product_url_obj = ProductURL.objects.filter(ean=ean).first()
    base_url = product_url_obj.url if product_url_obj else "https://via.placeholder.com/150"

    results = [
        ProductDetailsOut(
            ean=product.ean,
            sku=product.sku,
            loja=product.loja,
            preco_final=product.preco_final,
            data_hora=product.data_hora,
            marketplace=product.marketplace,
            change_price=product.change_price,
            key_loja=product.key_loja,
            key_sku=product.key_sku,
            descricao=product.descricao,
            review=product.review,
            imagem=product.imagem,
            status=product.status,
            preco_pricing=product.preco_pricing,
            preco_buybox=product.preco_buybox,
            url=base_url,
            marca=product.marca,
            categoria=product.categoria,
            loja_normalizada=product.loja_normalizada,
        )
        for product in queryset
    ]

    logger.info(f"Retornando {len(results)} produto(s) ATIVO(S) para o EAN {ean}")
    return results

@api.get("/price_changes/", response=List[PriceChangeOut])
def get_price_changes(request, ean: Optional[str] = None, loja: Optional[str] = None):
    logger.info(f"Consultando alterações de preço: ean={ean}, loja={loja}")
    queryset = PriceChange.objects.exclude(preco_final_antigo__isnull=True)
    if ean:
        queryset = queryset.filter(ean=ean)
    if loja:
        queryset = queryset.filter(loja=loja)
    queryset = queryset.order_by('-timestamp')
    return [
        PriceChangeOut(
            ean=change.ean,
            loja=change.loja,
            key_loja=change.key_loja,
            preco_final_antigo=change.preco_final_antigo,
            preco_final_novo=change.preco_final_novo,
            timestamp=change.timestamp,
            url=change.url,
            descricao=change.descricao
        ) for change in queryset
    ]

@api.delete("/delproducts/", response={200: dict, 500: ErrorResponse})
def remove_all_products(request):
    try:
        logger.info("Iniciando exclusão de todos os produtos")
        count = ProductDetails.objects.all().delete()[0]
        logger.info(f"{count} produtos excluídos com sucesso")
        return 200, {"message": f"{count} produtos excluídos com sucesso"}
    except Exception as e:
        logger.error(f"Erro ao excluir produtos: {str(e)}")
        return 500, {"detail": f"Erro ao excluir produtos: {str(e)}"}

@api.delete("/urls/{ean_key}", response={200: dict, 404: ErrorResponse, 500: ErrorResponse})
def remove_url(request, ean_key: str):
    """
    Deleta uma URL específica pelo ean_key
    """
    try:
        logger.info(f"Tentando excluir URL com ean_key: {ean_key}")
        
        # Busca o objeto ou retorna 404
        try:
            url_obj = ProductURL.objects.get(ean_key=ean_key)
        except ProductURL.DoesNotExist:
            logger.warning(f"URL com ean_key {ean_key} não encontrada")
            return 404, {"detail": f"URL com ean_key '{ean_key}' não encontrada"}
        
        url_value = url_obj.url
        ean = url_obj.ean
        
        # Deleta o objeto
        url_obj.delete()
        
        logger.info(f"URL excluída com sucesso - EAN: {ean}, URL: {url_value}")
        return 200, {
            "message": "URL excluída com sucesso",
            "ean_key": ean_key,
            "ean": ean,
            "url": url_value
        }
        
    except Exception as e:
        logger.error(f"Erro ao excluir URL com ean_key {ean_key}: {str(e)}")
        return 500, {"detail": f"Erro ao excluir URL: {str(e)}"}

@api.patch('/products/update_precos', response={200: List[ProductDetailsOut], 404: ErrorResponse, 422: ErrorResponse})
def update_precos(request, payload: List[UpdatePrecosSchema]):
    """
    Atualiza preco_pricing e/ou preco_buybox de um ou mais produtos
    """
    try:
        logger.info(f"Recebendo {len(payload)} atualizações de preços")
        updated_products = []
        key_skus = [item.key_sku for item in payload]
        
        # Busca todos os produtos de uma vez
        products = ProductDetails.objects.filter(key_sku__in=key_skus)
        
        if not products.exists():
            logger.warning("Nenhum produto encontrado para os key_sku fornecidos")
            return 404, {"detail": "Nenhum produto encontrado"}
        
        # Cria um dicionário para acesso rápido
        products_dict = {p.key_sku: p for p in products}
        
        for item in payload:
            # Valida o schema
            try:
                item.validate()
            except ValidationError as e:
                return 422, {"detail": str(e)}
            
            if item.key_sku not in products_dict:
                logger.warning(f"Produto {item.key_sku} não encontrado")
                continue
            
            product = products_dict[item.key_sku]
            campos_atualizados = []
            
            # ✅ CORREÇÃO: Validação entre preco_pricing e preco_buybox
            # Regra: preco_pricing (mínimo) NÃO pode ser maior que preco_buybox (seu preço)
            if item.preco_pricing is not None and item.preco_buybox is not None:
                if item.preco_pricing > item.preco_buybox:
                    logger.warning(
                        f"preco_pricing (R$ {item.preco_pricing:.2f}) maior que preco_buybox "
                        f"(R$ {item.preco_buybox:.2f}) para {item.key_sku}"
                    )
                    return 422, {
                        "detail": "preco_pricing não pode ser maior que preco_buybox",
                        "data": {
                            "key_sku": item.key_sku,
                            "preco_pricing_enviado": float(item.preco_pricing),
                            "preco_buybox_enviado": float(item.preco_buybox)
                        }
                    }
            
            # Validação quando apenas preco_pricing é atualizado
            elif item.preco_pricing is not None and item.preco_buybox is None:
                # Verifica contra o preco_buybox existente no banco
                if product.preco_buybox and item.preco_pricing > product.preco_buybox:
                    logger.warning(
                        f"preco_pricing (R$ {item.preco_pricing:.2f}) maior que preco_buybox atual "
                        f"(R$ {product.preco_buybox:.2f}) para {item.key_sku}"
                    )
                    return 422, {
                        "detail": "preco_pricing não pode ser maior que preco_buybox atual",
                        "data": {
                            "key_sku": item.key_sku,
                            "preco_pricing_enviado": float(item.preco_pricing),
                            "preco_buybox_atual": float(product.preco_buybox)
                        }
                    }
            
            # Validação quando apenas preco_buybox é atualizado
            elif item.preco_buybox is not None and item.preco_pricing is None:
                # Verifica contra o preco_pricing existente no banco
                if product.preco_pricing and product.preco_pricing > item.preco_buybox:
                    logger.warning(
                        f"preco_pricing atual (R$ {product.preco_pricing:.2f}) maior que preco_buybox novo "
                        f"(R$ {item.preco_buybox:.2f}) para {item.key_sku}"
                    )
                    return 422, {
                        "detail": "preco_buybox não pode ser menor que preco_pricing atual",
                        "data": {
                            "key_sku": item.key_sku,
                            "preco_pricing_atual": float(product.preco_pricing),
                            "preco_buybox_enviado": float(item.preco_buybox)
                        }
                    }
            
            # Atualiza preco_pricing se fornecido
            if item.preco_pricing is not None:
                product.preco_pricing = item.preco_pricing
                campos_atualizados.append(f"preco_pricing: R$ {item.preco_pricing:.2f}")
            
            # Atualiza preco_buybox se fornecido
            if item.preco_buybox is not None:
                product.preco_buybox = item.preco_buybox
                campos_atualizados.append(f"preco_buybox: R$ {item.preco_buybox:.2f}")
            
            updated_products.append(product)
            
            logger.info(
                f"Atualizando preços:\n"
                f"- Key SKU: {item.key_sku}\n"
                f"- Loja: {product.loja}\n"
                f"- Produto: {product.descricao[:50]}\n"
                f"- Preço Final (mercado): R$ {product.preco_final:.2f}\n"
                f"- Campos atualizados: {', '.join(campos_atualizados)}\n"
            )
        
        # Determina quais campos atualizar
        fields_to_update = []
        if any(item.preco_pricing is not None for item in payload):
            fields_to_update.append('preco_pricing')
        if any(item.preco_buybox is not None for item in payload):
            fields_to_update.append('preco_buybox')
        
        # Atualiza todos de uma vez
        ProductDetails.objects.bulk_update(updated_products, fields_to_update)
        
        logger.info(f"{len(updated_products)} produtos atualizados com sucesso")
        
        eans = {p.ean for p in updated_products}
        url_map = {
            pu.ean: pu.url
            for pu in ProductURL.objects.filter(ean__in=eans)
        }

        result = [
            ProductDetailsOut(
                ean=product.ean,
                sku=product.sku,
                key_sku=product.key_sku,
                loja=product.loja,
                preco_final=product.preco_final,
                data_hora=product.data_hora,
                marketplace=product.marketplace,
                change_price=product.change_price,
                key_loja=product.key_loja,
                descricao=product.descricao,
                review=product.review,
                imagem=product.imagem,
                status=product.status,
                preco_pricing=product.preco_pricing,
                preco_buybox=product.preco_buybox,
                url=url_map.get(product.ean, "https://via.placeholder.com/150"),
                marca=product.marca,
                categoria=product.categoria,
                loja_normalizada=product.loja_normalizada,
            )
            for product in updated_products
        ]

        return 200, result
        
    except Exception as e:
        logger.error(f"Erro ao atualizar preços: {str(e)}")
        return 422, {"detail": f"Erro ao atualizar preços: {str(e)}"}

@api.patch('/urls/update_is_active', response={200: List[SchemaProductURL], 422: ErrorResponse})
def update_is_active(request, payload: List[UpdateIsActiveSchema]):
    try:
        logger.info(f"Recebendo {len(payload)} atualizações para is_active")
        updated_products = []
        ean_keys = [item.ean_key for item in payload]
        products = ProductURL.objects.filter(ean_key__in=ean_keys)
        
        if not products.exists():
            logger.warning("Nenhum produto encontrado para os ean_keys fornecidos")
            return 422, {"detail": "Nenhum produto encontrado para os ean_keys fornecidos"}

        for item in payload:
            try:
                product = products.get(ean_key=item.ean_key)
                product.is_active = item.is_active
                updated_products.append(product)
                logger.info(f"Atualizando is_active para {item.is_active} no produto {item.ean_key}")
            except ProductURL.DoesNotExist:
                logger.warning(f"Produto com ean_key {item.ean_key} não encontrado")
                continue

        ProductURL.objects.bulk_update(updated_products, ['is_active'])
        return updated_products
    except Exception as e:
        logger.error(f"Erro ao atualizar is_active: {str(e)}")
        return 422, {"detail": f"Erro ao atualizar is_active: {str(e)}"}

@api.post("/products", response={200: List[ProductDetailsOut], 422: ErrorResponse})
def create_products(request, products: List[ProductsDetailsIn]):
    created_products = []
    
    if not products:
        logger.warning("Lista de produtos vazia recebida")
        return []

    ean = products[0].ean
    marketplace = products[0].marketplace
    
    logger.info(f"Processando {len(products)} produtos - EAN: {ean}, Marketplace: {marketplace}")
    
    # Buscar produtos existentes APENAS do mesmo EAN + MARKETPLACE
    existing_sellers = list(ProductDetails.objects.filter(ean=ean, marketplace=marketplace))
    existing_keys = {s.key_sku for s in existing_sellers}
    received_keys = {p.key_sku for p in products}

    existing_by_key = {s.key_sku: s for s in existing_sellers}

    logger.info(f"Produtos existentes no banco: {len(existing_keys)}")
    logger.info(f"Produtos recebidos na raspagem: {len(received_keys)}")

    # === PROCESSAR PRODUTOS RECEBIDOS ===
    for product in products:
        try:
            product_data = product.dict()
            product_data["preco_final"] = Decimal(str(product_data["preco_final"]))
            product_data["preco_pricing"] = (
                Decimal(str(product_data["preco_pricing"]))
                if product_data.get("preco_pricing") not in [None, "", 0]
                else None
            )
            product_data["data_hora"] = timezone.now()

            product_data["loja_normalizada"] = normalizar_loja(product_data.get("loja"))

            logger.info(
                f"Processando produto:\n"
                f"- Seller: {product_data['loja']}\n"
                f"- Loja Normalizada: {product_data['loja_normalizada']}\n"
                f"- EAN: {product_data['ean']}\n"
                f"- Key SKU: {product_data['key_sku']}\n"
                f"- Preço: R$ {product_data['preco_final']:.2f}\n"
                f"- Marketplace: {product_data['marketplace']}\n"
            )

            # === DETECTAR MUDANÇA DE PREÇO ===
            existing_product = existing_by_key.get(product_data["key_sku"])
            if existing_product:
                product_data["change_price"] = existing_product.change_price
                if round(existing_product.preco_final, 2) != round(product_data["preco_final"], 2):
                    product_data["change_price"] += 1
                    PriceChange.objects.create(
                        ean=product_data["ean"],
                        loja=product_data["loja"],
                        key_loja=product_data["key_loja"],
                        preco_final_antigo=existing_product.preco_final,
                        preco_final_novo=product_data["preco_final"],
                        timestamp=timezone.now(),
                        url=product_data["url"],
                        descricao=product_data["descricao"]
                    )
                    logger.info(
                        f"Mudança de preço detectada!\n"
                        f"- Seller: {product_data['loja']}\n"
                        f"- EAN: {product_data['ean']}\n"
                        f"- Preço anterior: R$ {existing_product.preco_final:.2f}\n"
                        f"- Novo preço: R$ {product_data['preco_final']:.2f}\n"
                        f"- Total de mudanças: {product_data['change_price']}\n"
                    )
            else:
                product_data["change_price"] = 0
                PriceChange.objects.create(
                    ean=product_data["ean"],
                    loja=product_data["loja"],
                    key_loja=product_data["key_loja"],
                    preco_final_antigo=None,
                    preco_final_novo=product_data["preco_final"],
                    timestamp=timezone.now(),
                    url=product_data["url"],
                    descricao=product_data["descricao"]
                )
                logger.info(
                    f"Primeiro preço registrado:\n"
                    f"- Seller: {product_data['loja']}\n"
                    f"- Loja Normalizada: {product_data['loja_normalizada']}\n"
                    f"- EAN: {product_data['ean']}\n"
                    f"- Key SKU: {product_data['key_sku']}\n"
                    f"- Preço: R$ {product_data['preco_final']:.2f}\n"
                )

            # === SALVAR/ATUALIZAR PRODUTO ===
            product_data["status"] = "ativo"
            new_product, created = ProductDetails.objects.update_or_create(
                key_sku=product_data["key_sku"],
                defaults=product_data
            )
            created_products.append((new_product, product_data["url"]))

        except Exception as e:
            logger.error(f"Erro ao salvar produto {product_data.get('key_sku', 'N/A')}: {str(e)}")
            return 422, {"detail": f"Erro ao salvar produto: {str(e)}", "data": product_data}

    # === INATIVAR PRODUTOS QUE NÃO VIERAM NA RASPAGEM ===
    current_time = timezone.now()
    inactivated_count = 0

    product_url_obj = ProductURL.objects.filter(ean=ean).first()
    base_url = product_url_obj.url if product_url_obj else "https://via.placeholder.com/150"

    to_inactivate = []
    for existing in existing_sellers:
        if existing.key_sku not in received_keys:
            existing.status = "inativo"
            existing.data_hora = current_time
            to_inactivate.append(existing)
            created_products.append((existing, base_url))
            inactivated_count += 1

    if to_inactivate:
        ProductDetails.objects.bulk_update(to_inactivate, ['status', 'data_hora'])
        logger.info(f"Inativados: {[p.key_sku for p in to_inactivate]}")

    logger.info(f"Resumo: {len(products)} ativos, {inactivated_count} inativados")

    # === RETORNAR RESPOSTA COM URL CORRETA ===
    return [
        ProductDetailsOut(
            ean=p.ean,
            sku=p.sku,
            loja=p.loja,
            preco_final=p.preco_final,
            data_hora=p.data_hora,
            marketplace=p.marketplace,
            change_price=p.change_price,
            key_loja=p.key_loja,
            key_sku=p.key_sku,
            descricao=p.descricao,
            review=p.review,
            imagem=p.imagem,
            status=p.status,
            preco_pricing=p.preco_pricing,
            preco_buybox=p.preco_buybox,
            url=url,
            marca=p.marca,
            categoria=p.categoria,
            loja_normalizada=p.loja_normalizada
        ) for p, url in created_products
    ]

@api.post('urls', response=List[SchemaProductURL])
def post_urls(request, payload: List[ProductURLInputSchema]):
    logger.info(f"Recebendo {len(payload)} URLs para salvar")
    created_products = []
    for url_data in payload:
        try:
            product = ProductURL(
                ean_key=url_data.ean_key,
                ean=url_data.ean,
                brand=url_data.brand,
                url=url_data.url,
                client_name=url_data.client_name,
                client=None,
                is_active=url_data.is_active
            )
            created_products.append(product)
        except IntegrityError as e:
            logger.error(f"Erro ao salvar produto (possível duplicata): {e}")
            continue
    ProductURL.objects.bulk_create(created_products, ignore_conflicts=True)
    created_eans = [url_data.ean for url_data in payload]
    logger.info(f"Produtos salvos: {created_eans}")
    return ProductURL.objects.filter(ean__in=created_eans)