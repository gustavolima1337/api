import logging
from ninja import NinjaAPI, ModelSchema, Schema
from typing import List, Optional
from .models import ProductURL, ProductDetails, PriceChange
from ninja.errors import ValidationError
from django.db import IntegrityError
from django.http import HttpResponse
from django.core.exceptions import ObjectDoesNotExist
from decimal import Decimal
from datetime import datetime
from django.utils import timezone

# Configura o logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

api = NinjaAPI()

class ErrorResponse(Schema):
    detail: str
    data: Optional[dict] = None

class ProductsDetailsIn(ModelSchema):
    class Config:
        model = ProductDetails
        model_fields = [
            'ean', 'sku', 'key_sku', 'loja', 'preco_final', 'data_hora', 'marketplace',
            'key_loja', 'descricao', 'review', 'imagem', 'status',
            'preco_pricing', 'url', 'marca'
        ]

class ProductDetailsOut(Schema):
    ean: str
    sku: str
    loja: str
    preco_final: Decimal
    data_hora: datetime
    marketplace: str
    change_price: int
    key_loja: str
    descricao: str
    review: float
    imagem: str
    status: str
    preco_pricing: Optional[Decimal]
    url: str
    marca: str

class PriceChangeOut(Schema):
    ean: str
    loja: str
    key_loja: str
    preco_final_antigo: Optional[Decimal]
    preco_final_novo: Decimal
    timestamp: datetime
    url: str
    descricao: str

class ProductURLInputSchema(Schema):
    ean_key: str
    ean: str
    brand: str
    url: str
    client_name: str

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

class SchemaProductURL(ModelSchema):
    class Config:
        model = ProductURL
        model_fields = ['ean_key', 'ean', 'brand', 'url', 'client', 'created_at', 'client_name']

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
                client=None
            )
            created_products.append(product)
        except IntegrityError as e:
            logger.error(f"Erro ao salvar produto (possível duplicata): {e}")
            continue
    ProductURL.objects.bulk_create(created_products, ignore_conflicts=True)
    created_eans = [url_data.ean for url_data in payload]
    logger.info(f"Produtos salvos: {created_eans}")
    return ProductURL.objects.filter(ean__in=created_eans)

@api.get('urls/', response=List[SchemaProductURL])
def get_urls(request):
    return ProductURL.objects.all()

@api.get('products/', response=List[ProductDetailsOut])
def get_products(request):
    return ProductDetails.objects.all()

@api.post("/products", response={200: List[ProductDetailsOut], 422: ErrorResponse})
def create_products(request, products: List[ProductsDetailsIn]):
    created_products = []
    ean = products[0].ean if products else None  # Assume que todos os produtos têm o mesmo EAN

    # Obter todos os sellers existentes para o EAN
    existing_sellers = ProductDetails.objects.filter(ean=ean) if ean else ProductDetails.objects.none()
    existing_keys = {s.key_sku for s in existing_sellers}
    received_keys = {p.key_sku for p in products}

    # Processar produtos recebidos
    for product in products:
        try:
            product_data = product.dict()

            # Converter preco_final e preco_pricing para Decimal
            product_data["preco_final"] = Decimal(product_data["preco_final"])
            if product_data["preco_pricing"]:
                product_data["preco_pricing"] = Decimal(product_data["preco_pricing"])
            else:
                product_data["preco_pricing"] = None
            # Garantir que data_hora seja um objeto datetime
            product_data["data_hora"] = timezone.now()

            # Registrar log informativo para cada produto
            logger.info(
                f"Processando produto:\n"
                f"- Seller: {product_data['loja']}\n"
                f"- Produto EAN: {product_data['ean']}\n"
                f"- Preço: R$ {product_data['preco_final']:.2f}\n"
                f"- Total de mudanças: {product_data.get('change_price', 0)}\n"
                f"- Marketplace: {product_data['marketplace']}\n"
            )

            # Verifica se o produto já existe pelo key_sku
            existing_product = ProductDetails.objects.filter(key_sku=product_data["key_sku"]).first()
            if existing_product:
                # Preserva o change_price atual
                product_data["change_price"] = existing_product.change_price
                # Verifica mudança de preço com arredondamento para duas casas decimais
                if round(existing_product.preco_final, 2) != round(product_data["preco_final"], 2):
                    product_data["change_price"] += 1
                    # Registrar a alteração no modelo PriceChange
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
                # Novo produto, inicializa change_price
                product_data["change_price"] = 0
                # Registrar o primeiro preço como uma alteração
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
                    f"- Preço: R$ {product_data['preco_final']:.2f}\n"
                    f"- URL: {product_data['url']}\n"
                )

            # Define status como ativo
            product_data["status"] = "ativo"

            # Cria ou atualiza o produto
            new_product, created = ProductDetails.objects.update_or_create(
                key_sku=product_data["key_sku"],
                defaults=product_data
            )
            created_products.append((new_product, product_data["url"]))
        except Exception as e:
            logger.error(f"Erro ao salvar produto {product_data['key_sku']}: {str(e)}")
            return 422, {"detail": f"Erro ao salvar produto: {str(e)}", "data": product_data}

    # Marcar sellers ausentes como inativo
    current_time = timezone.now()
    for existing in existing_sellers:
        if existing.key_sku not in received_keys:
            existing.status = "inativo"
            existing.data_hora = current_time
            existing.save()
            product_url = ProductURL.objects.filter(ean=existing.ean).first()
            url = product_url.url if product_url else "https://via.placeholder.com/150"
            created_products.append((existing, url))

    # Retornar os produtos no formato do schema ProductDetailsOut
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
            url=url,
            marca=p.marca
        ) for p, url in created_products
    ]

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
