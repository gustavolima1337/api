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
from django.shortcuts import get_object_or_404

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
            'preco_pricing', 'preco_buybox', 'url', 'marca','categoria'

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

@api.get('urls/', response=List[SchemaProductURL])
def get_urls(request):
    return ProductURL.objects.all()

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

@api.get('products/', response=List[ProductDetailsOut])
def get_products(request):
    return ProductDetails.objects.all()

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
            categoria=p.categoria
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
            
            # Atualiza preco_pricing se fornecido
            if item.preco_pricing is not None:
                # Validação: preço mínimo não pode ser maior que preço final
                if item.preco_pricing > product.preco_final:
                    logger.warning(
                        f"preco_pricing (R$ {item.preco_pricing:.2f}) maior que preco_final "
                        f"(R$ {product.preco_final:.2f}) para {item.key_sku}"
                    )
                    return 422, {
                        "detail": "preco_pricing não pode ser maior que preco_final",
                        "data": {
                            "key_sku": item.key_sku,
                            "preco_pricing_enviado": float(item.preco_pricing),
                            "preco_final_atual": float(product.preco_final)
                        }
                    }
                
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
                f"- Preço Final: R$ {product.preco_final:.2f}\n"
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
                loja=product.loja,
                preco_final=product.preco_final,
                key_sku=product.key_sku,
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