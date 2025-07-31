import asyncio
import httpx
import pandas as pd
from amazon_scraper import amazon_scrap
from beleza_scraper import beleza_na_web_scrap
from magalu_scraper import magalu_scrap
from epoca_scraper import epoca_scrap
from decimal import Decimal
import logging
from datetime import datetime, timezone, timedelta
import time
import random
import json
import os

# Configura o logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

API_ENDPOINT = "http://201.23.64.234:8000/api/urls/"
PRODUCTS_ENDPOINT = "http://201.23.64.234:8000/api/products"
PRICE_HISTORY_FILE = "price_history.json"

# Limite de concorrência para melhorar a velocidade
CONCURRENCY_LIMIT = 20
REQUEST_TIMEOUT = 8

def load_price_history():
    """Carrega o histórico de preços do arquivo JSON."""
    if os.path.exists(PRICE_HISTORY_FILE):
        try:
            with open(PRICE_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error("Erro ao carregar histórico de preços: %s", e)
            return []
    return []

def save_price_history(history):
    """Salva o histórico de preços no arquivo JSON."""
    try:
        with open(PRICE_HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except IOError as e:
        logger.error("Erro ao salvar histórico de preços: %s", e)

def log_price_change(product, old_preco_final=None):
    """Registra uma alteração de preço no histórico."""
    history = load_price_history()
    new_entry = {
        "ean": product["ean"],
        "loja": product.get("loja", "-"),
        "key_loja": product.get("key_loja", "sem_loja"),
        "preco_final_antigo": str(old_preco_final) if old_preco_final is not None else None,
        "preco_final_novo": str(product.get("preco_final", product.get("price", 0.00))),
        "timestamp": datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3))).isoformat(),
        "url": product.get("url", "-"),
        "descricao": product.get("descricao", "Sem descrição")
    }
    history.append(new_entry)
    save_price_history(history)
    logger.info("Alteração de preço registrada para EAN %s, loja %s: preco_final %s -> %s",
                product["ean"], product.get("loja", "-"), old_preco_final, product.get("preco_final", product.get("price")))

async def get_from_api(client):
    """Obtém dados da API e retorna um DataFrame."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logger.info("Iniciando requisição GET para: %s", API_ENDPOINT)
            response = await client.get(API_ENDPOINT, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429:
                wait_time = 2 ** attempt * random.uniform(2, 5)
                logger.warning("[API] Erro 429 Too Many Requests na tentativa %d/%d para GET API, esperando %.2f segundos", 
                              attempt + 1, max_retries, wait_time)
                await asyncio.sleep(wait_time)
                continue
            if response.status_code != 200:
                logger.error("Erro na API: %s", response.status_code)
                return None
            response_data = response.json()
            if isinstance(response_data, list):
                return pd.DataFrame(response_data)
            logger.warning("Resposta não é uma lista")
            return None
        except httpx.RequestError as e:
            logger.error("Erro ao conectar com a API: %s", e)
            if attempt < max_retries - 1:
                await asyncio.sleep(random.uniform(2, 5))
            else:
                logger.error("[API] Falha após %d tentativas para GET API", max_retries)
                return None
        except ValueError as e:
            logger.error("Erro ao processar JSON: %s", e)
            return None

async def post_to_products(products, client):
    """Envia uma lista de produtos para o endpoint POST /products."""
    max_retries = 3
    history = load_price_history()
    
    for attempt in range(max_retries):
        try:
            payload = []
            for product in products:
                # Buscar preço final anterior para este EAN e loja
                last_entry = next((entry for entry in reversed(history) 
                                 if entry["ean"] == product["ean"] and entry["loja"] == product.get("loja", "-")), None)
                
                old_preco_final = Decimal(last_entry["preco_final_novo"]) if last_entry else None
                new_preco_final = Decimal(str(product.get("preco_final", product.get("price", 0.00))))
                
                # Verificar se houve alteração no preco_final
                price_changed = (
                    (old_preco_final is not None and old_preco_final != new_preco_final) or
                    (old_preco_final is None and new_preco_final != 0.00)
                )
                
                if price_changed:
                    log_price_change(product, old_preco_final)
                
                product_data = {
                    "ean": product["ean"],
                    "sku": product.get("sku", "SKU não encontrado"),
                    "loja": product.get("loja", "-"),
                    "preco_final": str(Decimal(str(product.get("preco_final", 0.00)))),
                    "marketplace": product.get("marketplace", "Desconhecido"),
                    "key_loja": product.get("key_loja", "sem_loja"),
                    "key_sku": product.get("key_sku", f"{product['ean']}_{product.get('loja', 'sem_loja')}"),
                    "descricao": product.get("descricao", "Sem descrição"),
                    "review": float(product.get("review", 0.0)),
                    "imagem": product.get("imagem", "https://via.placeholder.com/150"),
                    "status": product.get("status", "ativo"),
                    "preco_pricing": str(Decimal(str(product["preco_pricing"]))) if product.get("preco_pricing") else None,
                    "url": product.get("url", "-"),
                    "marca": product.get("marca", "Marca não informada")
                }
                if "price" in product and "preco_final" not in product:
                    product_data["preco_final"] = str(Decimal(str(product["price"])))
                if "image" in product and "imagem" not in product:
                    product_data["imagem"] = product["image"]
                payload.append(product_data)
            
            logger.info("Enviando %s produtos para %s", len(payload), PRODUCTS_ENDPOINT)
            response = await client.post(PRODUCTS_ENDPOINT, json=payload, timeout=REQUEST_TIMEOUT)
            if response.status_code == 429:
                wait_time = 2 ** attempt * random.uniform(2, 5)
                logger.warning("[API] Erro 429 Too Many Requests na tentativa %d/%d para POST produtos, esperando %.2f segundos", 
                              attempt + 1, max_retries, wait_time)
                await asyncio.sleep(wait_time)
                continue
            if response.status_code == 200:
                logger.info("Produtos enviados com sucesso")
                return response.json()
            logger.error("Erro ao enviar produtos: %s", response.status_code)
            return None
        except (httpx.RequestError, ValueError) as e:
            logger.error("Erro ao enviar produtos: %s", e)
            if attempt < max_retries - 1:
                await asyncio.sleep(random.uniform(2, 5))
            else:
                logger.error("[API] Falha após %d tentativas para POST produtos", max_retries)
                return None

async def scrape_url(row, semaphore, client, scrape_stats, df):
    """Processa uma única linha do DataFrame, executando os scrapers apropriados."""
    async with semaphore:
        ean = row['ean']
        url = row['url']
        brand = row['brand']
        start_time = time.time()
        
        if not url or not isinstance(url, str):
            logger.warning("URL inválida para EAN %s, ignorando", ean)
            scrape_stats[ean] = {"time": 0, "error": "URL inválida", "function": None, "url": url, "brand": brand}
            return None

        logger.info("Processando EAN: %s", ean)
        results = []
        try:
            if "amazon" in url.lower():
                logger.info("Executando amazon_scrap para EAN: %s", ean)
                try:
                    amazon_result = await amazon_scrap(url, ean, brand)
                    if amazon_result:
                        results.extend(amazon_result)
                        logger.info("Resultados obtidos do Amazon para EAN %s", ean)
                except Exception as e:
                    logger.error("Erro no amazon_scrap para EAN %s: %s", ean, e)
                    scrape_stats[ean] = {"time": time.time() - start_time, "error": str(e), "function": "amazon_scrap", "url": url, "brand": brand}
                    return None
            elif "belezanaweb" in url.lower():
                logger.info("Executando beleza_na_web_scrap, epoca_scrap e magalu_scrap para EAN: %s", ean)
                beleza_task = beleza_na_web_scrap(url, ean, brand)
                epoca_task = epoca_scrap(ean, brand)
                magalu_task = magalu_scrap(ean, brand)
                tasks = [beleza_task, epoca_task, magalu_task]
                task_names = ["beleza_na_web_scrap", "epoca_scrap", "magalu_scrap"]
                
                task_results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for task_name, result in zip(task_names, task_results):
                    if isinstance(result, list) and result:
                        results.extend(result)
                        logger.info("Resultados obtidos do %s para EAN %s", task_name.replace("_scrap", "").capitalize(), ean)
                    elif isinstance(result, Exception):
                        logger.error("Erro no %s para EAN %s: %s", task_name, ean, result)
                        scrape_stats[ean] = {"time": time.time() - start_time, "error": str(result), "function": task_name, "url": url, "brand": brand}
                    else:
                        logger.warning("Nenhum resultado retornado do %s para EAN %s", task_name, ean)
                        scrape_stats[ean] = {"time": time.time() - start_time, "error": f"Nenhum resultado retornado por {task_name}", "function": task_name, "url": url, "brand": brand}

                if results:
                    logger.info("Enviando %s resultados para EAN %s", len(results), ean)
                    await post_to_products(results, client)
                    scrape_stats[ean] = {"time": time.time() - start_time, "error": None, "function": None, "url": url, "brand": brand}
                else:
                    if ean not in scrape_stats or scrape_stats[ean]["error"]:
                        scrape_stats[ean] = {"time": time.time() - start_time, "error": "Nenhum resultado retornado por nenhum scraper", "function": "all_scrapers", "url": url, "brand": brand}
                    return None
                
            if results:
                return results
            return None
        except Exception as e:
            logger.error("Erro geral ao processar EAN %s: %s", ean, e)
            scrape_stats[ean] = {"time": time.time() - start_time, "error": str(e), "function": "general_scrape", "url": url, "brand": brand}
            return None

async def retry_failed_eans(scrape_stats, semaphore, client, df):
    """Tenta novamente os EANs que falharam usando epoca_scrap e magalu_scrap, com até 2 tentativas."""
    errors = [ean for ean, stats in scrape_stats.items() if stats["error"]]
    if not errors:
        logger.info("Nenhum EAN com erro para retry.")
        return [], []

    logger.info("Tentando novamente %d EANs com erro", len(errors))
    retry_results = []
    retry_stats = {}
    max_retries = 2  # Limite de tentativas para cada EAN

    for ean in errors:
        async with semaphore:
            brand = scrape_stats[ean].get("brand", "Desconhecida")
            for attempt in range(max_retries):
                start_time = time.time()
                logger.info("Reexecutando epoca_scrap e magalu_scrap para EAN %s (tentativa %d/%d)", ean, attempt + 1, max_retries)
                results = []
                try:
                    epoca_task = epoca_scrap(ean, brand)
                    magalu_task = magalu_scrap(ean, brand)
                    epoca_result, magalu_result = await asyncio.gather(epoca_task, magalu_task, return_exceptions=True)

                    if isinstance(epoca_result, list) and epoca_result:
                        results.extend(epoca_result)
                        logger.info("Resultados obtidos do Época (retry, tentativa %d) para EAN %s", attempt + 1, ean)
                    elif isinstance(epoca_result, Exception):
                        logger.error("Erro no epoca_scrap (retry, tentativa %d) para EAN %s: %s", attempt + 1, ean, epoca_result)

                    if isinstance(magalu_result, list) and magalu_result:
                        results.extend(magalu_result)
                        logger.info("Resultados obtidos do Magalu (retry, tentativa %d) para EAN %s", attempt + 1, ean)
                    elif isinstance(magalu_result, Exception):
                        logger.error("Erro no magalu_scrap (retry, tentativa %d) para EAN %s: %s", attempt + 1, ean, magalu_result)

                    if results:
                        logger.info("Enviando %s resultados de retry para EAN %s (tentativa %d)", len(results), ean, attempt + 1)
                        await post_to_products(results, client)
                        retry_stats[ean] = {"time": time.time() - start_time, "error": None, "function": None, "url": None, "brand": brand}
                        retry_results.extend(results)
                        break  # Sai do loop de retries se houver sucesso
                    else:
                        logger.warning("Nenhum resultado retornado no retry para EAN %s (tentativa %d)", ean, attempt + 1)
                        if attempt == max_retries - 1:
                            retry_stats[ean] = {"time": time.time() - start_time, "error": "Nenhum resultado retornado após retries", "function": None, "url": None, "brand": brand}
                        await asyncio.sleep(random.uniform(2, 5))  # Backoff antes da próxima tentativa

                except Exception as e:
                    logger.error("Erro geral no retry para EAN %s (tentativa %d): %s", ean, attempt + 1, e)
                    if attempt == max_retries - 1:
                        retry_stats[ean] = {"time": time.time() - start_time, "error": str(e), "function": "retry_general", "url": None, "brand": brand}
                    await asyncio.sleep(random.uniform(2, 5))  # Backoff antes da próxima tentativa

    return retry_stats, retry_results

def save_report(scrape_stats, retry_stats, total_time):
    """Salva um relatório em formato txt com tempos, erros e retries."""
    total_eans = len(scrape_stats)
    errors = [ean for ean, stats in scrape_stats.items() if stats["error"]]
    error_count = len(errors)
    retry_success = [ean for ean, stats in retry_stats.items() if stats["error"] is None]
    retry_errors = [ean for ean, stats in retry_stats.items() if stats["error"]]

    with open("scrape_report.txt", "w", encoding="utf-8") as f:
        f.write(f"Relatório de Scraping - {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
        f.write("=" * 80 + "\n\n")
        
        f.write("Tempos de Execução por EAN (Primeira Tentativa):\n")
        f.write("-" * 40 + "\n")
        for ean, stats in scrape_stats.items():
            time_taken = stats["time"]
            status = "Sucesso" if not stats["error"] else f"Erro: {stats['error']}"
            function = stats["function"] if stats["function"] else "N/A"
            url = stats["url"] if stats["url"] else "N/A"
            brand = stats["brand"] if stats["brand"] else "N/A"
            f.write(f"EAN: {ean} | URL: {url} | Marca: {brand} | Tempo: {time_taken:.2f} segundos | Função: {function} | Status: {status}\n")
        
        f.write("\nTempos de Execução por EAN (Retries):\n")
        f.write("-" * 40 + "\n")
        for ean, stats in retry_stats.items():
            time_taken = stats["time"]
            status = "Sucesso" if not stats["error"] else f"Erro: {stats['error']}"
            function = stats["function"] if stats["function"] else "N/A"
            url = stats["url"] if stats["url"] else "N/A"
            brand = stats["brand"] if stats["brand"] else "N/A"
            f.write(f"EAN: {ean} | URL: {url} | Marca: {brand} | Tempo: {time_taken:.2f} segundos | Função: {function} | Status: {status}\n")
        
        f.write("\nResumo:\n")
        f.write("-" * 40 + "\n")
        f.write(f"Total de EANs processados (primeira tentativa): {total_eans}\n")
        f.write(f"Total de erros (primeira tentativa): {error_count}\n")
        f.write(f"Total de EANs reexecutados: {len(retry_stats)}\n")
        f.write(f"Total de retries bem-sucedidos: {len(retry_success)}\n")
        f.write(f"Total de erros nos retries: {len(retry_errors)}\n")
        f.write(f"Tempo total de execução: {total_time:.2f} segundos\n")
        
        if retry_errors:
            f.write("\nEANs com erro nos retries:\n")
            f.write("-" * 40 + "\n")
            for ean in retry_errors:
                stats = retry_stats[ean]
                function = stats["function"] if stats["function"] else "N/A"
                url = stats["url"] if stats["url"] else "N/A"
                brand = stats["brand"] if stats["brand"] else "N/A"
                f.write(f"EAN: {ean} | URL: {url} | Marca: {brand} | Função: {function} | Erro: {stats['error']}\n")
        else:
            f.write("\nNenhum EAN com erro nos retries.\n")

async def main():
    logger.info("Início da Execução - %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    start_total_time = time.time()
    
    scrape_stats = {}
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        df = await get_from_api(client)
        if df is None or df.empty:
            logger.warning("Nenhum dado válido retornado ou DataFrame vazio")
            save_report(scrape_stats, {}, time.time() - start_total_time)
            return None

        logger.info("Colunas do DataFrame: %s", df.columns.tolist())
        required_columns = ['url', 'ean', 'brand']
        if not all(col in df.columns for col in required_columns):
            logger.error("DataFrame não contém todas as colunas necessárias: %s", required_columns)
            save_report(scrape_stats, {}, time.time() - start_time)
            return None

        semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)
        results = []
        total_raspagens = len(df)
        raspagens_concluidas = 0
        
        for _, row in df.iterrows():
            result = await scrape_url(row, semaphore, client, scrape_stats, df)
            if result is not None:
                raspagens_concluidas += 1
            results.append(result)
        
        logger.info("Raspagens concluídas: %d de %d", raspagens_concluidas, total_raspagens)

        # Reexecutar EANs com erro usando epoca_scrap e magalu_scrap
        retry_stats, retry_results = await retry_failed_eans(scrape_stats, semaphore, client, df)
        results.extend(retry_results)

    total_time = time.time() - start_total_time
    save_report(scrape_stats, retry_stats, total_time)
    logger.info("Fim da Execução - %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    return df

if __name__ == "__main__":
    asyncio.run(main())
