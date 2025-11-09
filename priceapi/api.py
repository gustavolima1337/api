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

api = NinjaAPI()

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

class ErrorResponse(Schema):
    detail: str
    data: Optional[dict] = None

class ProductsDetailsIn(ModelSchema):
    class Config:
        model = ProductDetails
        model_fields = [
            'ean', 'sku', 'key_sku', 'loja', 'preco_final', 'data_hora', 'marketplace',
            'key_loja', 'descricao', 'review', 'imagem', 'status',
            'preco_pricing', 'preco_buybox', 'url', 'marca','categoria','loja_normalizada'

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
def get_products(request):
    return ProductDetails.objects.all()

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

@api.delete("/delproducts/")
def remove_all_products(request):
    try:
        logger.info("Iniciando exclusão de todos os produtos")
        count = ProductDetails.objects.all().delete()[0]
        logger.info(f"{count} produtos excluídos com sucesso")
        return {"message": f"{count} produtos excluídos com sucesso"}
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
    
@api.get("/products/{ean}", response=Dict[str, Any])
def get_product_by_ean(request, ean: str):
    """
    Busca APENAS produtos ATIVOS de um ÚNICO EAN.
    Retorna também um resumo agregando informações (ex: quantidade de sellers e faixa de preço).
    """

    if not ean.isdigit() or len(ean) != 13:
        logger.warning(f"EAN inválido recebido: {ean}")
        return 422, {"detail": "EAN deve ter exatamente 13 dígitos numéricos"}

    logger.info(f"Consulta de produtos ATIVOS para o EAN: {ean}")

    queryset = ProductDetails.objects.filter(
        ean=ean,
        status="ativo"
    ).order_by("-data_hora")

    if not queryset.exists():
        logger.info(f"Nenhum produto ATIVO encontrado para o EAN: {ean}")
        return 404, {"detail": f"Nenhum produto ativo encontrado para o EAN {ean}"}

    results = []
    precos = []

    for product in queryset:
        product_url_obj = ProductURL.objects.filter(ean=ean).first()
        url = product_url_obj.url if product_url_obj else "https://via.placeholder.com/150"

        preco = float(product.preco_final) if product.preco_final else 0
        precos.append(preco)

        results.append(
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
                url=url,
                marca=product.marca,
                categoria=product.categoria,
                loja_normalizada=product.loja_normalizada
            )
        )

    # 🔢 Bloco de resumo agregado
    resumo = {
        "ean": ean,
        "quantidade_sellers": len(results),
        "menor_preco": round(min(precos), 2) if precos else None,
        "maior_preco": round(max(precos), 2) if precos else None,
        "media_preco": round(sum(precos) / len(precos), 2) if precos else None,
        "marketplaces_encontrados": list({p.marketplace for p in queryset})
    }

    logger.info(f"Retornando {len(results)} produtos ativos para o EAN {ean}")

    return {
        "produtos": results,
        "resumo": resumo
    }

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
        
        # Busca URLs para retornar na resposta
        result = []
        for product in updated_products:
            product_url = ProductURL.objects.filter(ean=product.ean).first()
            url = product_url.url if product_url else "https://via.placeholder.com/150"
            
            result.append(ProductDetailsOut(
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
                url=url,
                marca=product.marca,
                categoria=product.categoria
            ))
        
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
    
    # ✅ CORREÇÃO: Buscar produtos existentes APENAS do mesmo EAN + MARKETPLACE
    existing_sellers = ProductDetails.objects.filter(
        ean=ean,
        marketplace=marketplace
    )
    existing_keys = {s.key_sku for s in existing_sellers}
    received_keys = {p.key_sku for p in products}
    
    logger.info(f"Produtos existentes no banco: {len(existing_keys)}")
    logger.info(f"Produtos recebidos na raspagem: {len(received_keys)}")
    logger.info(f"Keys existentes: {existing_keys}")
    logger.info(f"Keys recebidas: {received_keys}")

    # Processar produtos recebidos
    for product in products:
        try:
            product_data = product.dict()
            product_data["preco_final"] = Decimal(product_data["preco_final"])
            if product_data["preco_pricing"]:
                product_data["preco_pricing"] = Decimal(product_data["preco_pricing"])
            else:
                product_data["preco_pricing"] = None
            product_data["data_hora"] = timezone.now()

            logger.info(
                f"Processando produto:\n"
                f"- Seller: {product_data['loja']}\n"
                f"- EAN: {product_data['ean']}\n"
                f"- Key SKU: {product_data['key_sku']}\n"
                f"- Preço: R$ {product_data['preco_final']:.2f}\n"
                f"- Marketplace: {product_data['marketplace']}\n"
            )

            existing_product = ProductDetails.objects.filter(key_sku=product_data["key_sku"]).first()
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
                        f"- Produto EAN: {product_data['ean']}\n"
                        f"- Preço anterior: R$ {existing_product.preco_final:.2f}\n"
                        f"- Novo preço: R$ {product_data['preco_final']:.2f}\n"
                        f"- Total de mudanças: {product_data['change_price']}\n"
                        f"- Marketplace: {product_data['marketplace']}\n"
                        f"- URL: {product_data['url']}\n"
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
                    f"Primeiro preço registrado para novo produto:\n"
                    f"- Seller: {product_data['loja']}\n"
                    f"- Produto EAN: {product_data['ean']}\n"
                    f"- Key SKU: {product_data['key_sku']}\n"
                    f"- Preço: R$ {product_data['preco_final']:.2f}\n"
                    f"- URL: {product_data['url']}\n"
                    f"- URL: {product_data['loja_normalizada']}\n"
                )

            product_data["status"] = "ativo"
            new_product, created = ProductDetails.objects.update_or_create(
                key_sku=product_data["key_sku"],
                defaults=product_data
            )
            created_products.append((new_product, product_data["url"]))
        except Exception as e:
            logger.error(f"Erro ao salvar produto {product_data.get('key_sku', 'N/A')}: {str(e)}")
            return 422, {"detail": f"Erro ao salvar produto: {str(e)}", "data": product_data}

    # ✅ CORREÇÃO: Inativar APENAS produtos do MESMO marketplace que não vieram na raspagem
    current_time = timezone.now()
    inactivated_count = 0
    
    for existing in existing_sellers:
        if existing.key_sku not in received_keys:
            existing.status = "inativo"
            existing.data_hora = current_time
            existing.save()
            
            product_url = ProductURL.objects.filter(ean=existing.ean).first()
            url = product_url.url if product_url else "https://via.placeholder.com/150"
            created_products.append((existing, url))
            inactivated_count += 1
            
            logger.info(
                f"Produto inativado (não retornou na raspagem):\n"
                f"- Seller: {existing.loja}\n"
                f"- EAN: {existing.ean}\n"
                f"- Key SKU: {existing.key_sku}\n"
                f"- Marketplace: {existing.marketplace}\n"
                f"- Status: inativo\n"
                f"- Loja normalizada: {existing.loja_normalizada}"
            )
    
    logger.info(f"Resumo: {len(products)} ativos, {inactivated_count} inativados")

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