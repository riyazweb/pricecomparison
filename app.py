from urllib.parse import urlparse # <-- Ensure this import exists
import requests
import re
import html
from urllib.parse import urlparse, parse_qs
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, redirect, url_for

# --- Flask App Initialization ---
app = Flask(__name__)

# --- Constants ---
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
REQUEST_TIMEOUT = 15

# --- Helper Functions (ID Extraction, API Fetch, Scraping) ---

def extract_pid_amazon(amazon_url):
    """Extracts the Product ID (ASIN) from Amazon URL."""
    match = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", amazon_url)
    return match.group(1) if match else None

def extract_pid_flipkart(flipkart_url):
    """Extracts the Product ID (pid) from Flipkart URL query parameters."""
    try:
        parsed_url = urlparse(flipkart_url)
        query_params = parse_qs(parsed_url.query)
        if 'pid' in query_params and query_params['pid']:
            return query_params['pid'][0]
    except Exception:
        pass
    match = re.search(r"pid=([A-Z0-9]+)", flipkart_url)
    return match.group(1) if match else None

def fetch_price_from_buyhatke(product_id, site_type):
    """
    Fetches price and details from BuyHatke API using product_id and site_type.
    Returns (product_name, price, tracker_url, thumbnails).
    """
    if site_type == "amazon":
        pos = 63
        site_prefix = "amazon"
    elif site_type == "flipkart":
        pos = 2
        site_prefix = "flipkart"
    else:
        print(f"❌ Unsupported site_type: {site_type}")
        return None, None, None, None

    buyhatke_api_url = f"https://buyhatke.com/api/productData?pos={pos}&pid={product_id}"
    headers = {"User-Agent": USER_AGENT}

    print(f"ℹ️ Querying BuyHatke API for {site_type.capitalize()} (PID: {product_id}): {buyhatke_api_url}")
    try:
        response = requests.get(buyhatke_api_url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        if data and "data" in data and data["data"] and isinstance(data["data"], dict):
            product_data = data["data"]
            product_name = product_data.get("name")
            price = product_data.get("cur_price")
            site_pos = product_data.get("site_pos")
            internal_pid = product_data.get("internalPid")
            
            # Extract thumbnail images if available
            thumbnails = product_data.get("thumbnailImages", [])
            if not thumbnails and "image" in product_data:
                # Fallback to main image if thumbnails not available
                thumbnails = [product_data["image"]]

            if product_name and site_pos is not None and internal_pid:
                slug = re.sub(r'[^\w-]+', '-', product_name.lower()).strip('-')
                if not slug: slug = f"product-{internal_pid}"
                buyhatke_url = f"https://buyhatke.com/{site_prefix}-{slug}-price-in-india-{site_pos}-{internal_pid}"

                if not buyhatke_url.startswith("https://buyhatke.com/"):
                     print(f"⚠️ Warning: Generated BuyHatke URL seems invalid: {buyhatke_url}")
                     return product_name, price, None, thumbnails
                return product_name, price, buyhatke_url, thumbnails
            else:
                 print(f"⚠️ Warning: Missing required fields in API response data for {site_type} pid={product_id}.")
                 return product_name, price, None, thumbnails # Return partial data if available
        else:
            print(f"❌ API response structure unexpected for {site_type} pid={product_id}.")
            print(f"   Response: {str(data)[:200]}...")
            return None, None, None, None

    except requests.exceptions.Timeout:
        print(f"❌ Timeout fetching data from BuyHatke API for {site_type} pid={product_id}.")
        return None, None, None, None
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching data from BuyHatke API: {e}")
        return None, None, None, None
    except ValueError as e: # JSON decode error
        print(f"❌ Error decoding JSON response from BuyHatke API: {e}")
        if 'response' in locals() and response:
             print(f"   Raw Response Text: {response.text[:200]}...")
        return None, None, None, None

# MODIFIED: Returns list of dicts or None/[]
def scrape_buyhatke_alternatives(tracker_url):
    """
    Downloads the BuyHatke tracker page and scrapes alternative prices.
    Returns a list of dictionaries, each containing seller, title, price, link.
    Returns None on download/parsing error, empty list if no items found.
    """
    if not tracker_url or not tracker_url.startswith("http"):
        print("ℹ️ Invalid or missing tracker URL provided. Cannot scrape alternatives.")
        return None # Indicate error

    print(f"\n⏬ Downloading BuyHatke page: {tracker_url}")
    headers = {"User-Agent": USER_AGENT}
    results = [] # Initialize list to store results

    try:
        response = requests.get(tracker_url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        print(f"✅ HTML Downloaded Successfully (Status: {response.status_code})")

        soup = BeautifulSoup(response.text, 'html.parser') # Use html.parser or html5lib

        # --- Find the price list (robust finding logic) ---
        price_list = None
        # Try specific ID first
        price_section_by_id = soup.find('section', id='onlineStoresList')
        if price_section_by_id:
            price_list = price_section_by_id.find('ul', class_=re.compile(r'my-4 grid'))

        # Try common structure/text if ID fails
        if not price_list:
            price_section_by_text = soup.find('section', class_='grid', string=lambda t: t and "Found" in t and "more prices" in t)
            if price_section_by_text:
                price_list = price_section_by_text.find('ul', class_=re.compile(r'my-4 grid'))

        # Generic fallback if specific methods fail
        if not price_list:
            all_lists = soup.find_all('ul', class_=re.compile(r'my-4 grid'))
            # Add logic here if needed to distinguish the correct list if multiple match
            if all_lists:
                 price_list = all_lists[0] # Assume the first one is correct for now

        # --- Process list items ---
        if not price_list:
            print("❌ Could not find the list (<ul>) of alternative prices. Scraping patterns might need update.")
            # Try dumping some HTML to debug if needed:
            # with open("debug_page.html", "w", encoding="utf-8") as f:
            #     f.write(response.text)
            # print("Saved debug_page.html")
            return [] # Return empty list, not an error, just no items found

        list_items = price_list.find_all('li', recursive=False)

        if not list_items:
            print("ℹ️ No alternative price list items (<li>) found within the list.")
            return [] # Return empty list

        print(f"\n🛒 Found {len(list_items)} Alternative Prices:")

        for item in list_items:
            seller_name = "N/A"
            product_title = "N/A"
            price_str = "N/A"
            buy_link = "#" # Default link to avoid errors

            # Seller Name Extraction
            img_container = item.find('div', class_=re.compile(r'\bflex\b.*\bitems-center\b'))
            if img_container:
                 img_tag = img_container.find('img', class_=re.compile(r'\brounded-full\b'), alt=True)
                 if img_tag and img_tag.get('alt'):
                      seller_name = img_tag['alt'].strip()
            if seller_name == "N/A" and img_container: # Fallback to src parsing
                img_tag = img_container.find('img', class_=re.compile(r'\brounded-full\b'), src=True)
                if img_tag:
                    src_url = img_tag.get('src', '')
                    match = re.search(r'/([^/]+?)(?:1|_m)?\.(?:png|jpe?g|webp|svg)', src_url, re.IGNORECASE)
                    if match:
                        raw_name = match.group(1)
                        seller_name = raw_name.replace('-', ' ').replace('_', ' ').title()
                        corrections = {'Vsales': 'Vijay Sales', 'Flipkart': 'Flipkart', 'Amazon': 'Amazon', 'Jiomart' : 'JioMart', 'Reliancedigital': 'Reliance Digital', 'Tatacliq': 'Tata CLiQ', 'Croma': 'Croma'}
                        seller_name = corrections.get(seller_name, seller_name)

            # Product Title Extraction
            title_p_tag = item.find('p', title=True)
            if title_p_tag: product_title = html.unescape(title_p_tag['title'].strip())
            else:
                 title_p_tag = item.find('p', class_='capitalize')
                 if title_p_tag:
                      # Prioritize the longer title if available (often more complete)
                      span_hidden_md = title_p_tag.find('span', class_='hidden md:inline')
                      span_md_hidden = title_p_tag.find('span', class_='md:hidden')
                      if span_hidden_md and len(span_hidden_md.get_text(strip=True)) > 5 : # Check length as heuristic
                           product_title = html.unescape(span_hidden_md.get_text(strip=True))
                      elif span_md_hidden:
                           product_title = html.unescape(span_md_hidden.get_text(strip=True))
                      else: # Fallback to the whole paragraph text
                           product_title = html.unescape(title_p_tag.get_text(strip=True))

            # Price Extraction
            price_container = item.find('div', class_=re.compile(r'flex justify-between'))
            if price_container:
                price_span = price_container.find('span', class_='font-bold')
                if price_span:
                    raw_price = price_span.get_text(strip=True)
                    price_match = re.search(r'([\d,]+(?:\.\d+)?)', raw_price)
                    if price_match: price_str = f"₹{price_match.group(1)}"
                    else: price_str = raw_price
                else:
                    price_p_tag = price_container.find('p', string=re.compile(r'₹'))
                    if price_p_tag:
                         raw_price = price_p_tag.get_text(strip=True)
                         price_match = re.search(r'₹\s*([\d,]+(?:\.\d+)?)', raw_price)
                         if price_match: price_str = f"₹{price_match.group(1)}"
                         else: price_str = raw_price

            # Buy Link Extraction
            # Look within price container first
            if price_container:
                buy_a_tag = price_container.find('a', href=True, class_=re.compile(r'\btext-primary\b'))
                if not buy_a_tag: buy_a_tag = price_container.find('a', href=True, string=re.compile(r'Buy'))
                if buy_a_tag: buy_link = buy_a_tag['href']

            # Fallback: Search entire list item if not found above
            if buy_link == "#": # Use default value check
                buy_a_tag = item.find('a', href=True, string=re.compile(r'Buy'))
                if buy_a_tag: buy_link = buy_a_tag['href']

            # Resolve relative URLs
            if buy_link.startswith('/'):
                 buy_link = requests.compat.urljoin(tracker_url, buy_link)

            # Final Fallback: Seller from Buy Link Hostname
            if seller_name == "N/A" and buy_link != "#" and buy_link.startswith("http"):
                try:
                    target_url = buy_link
                    parsed_buy_link = urlparse(buy_link)
                    if 'tracking.buyhatke.com' in parsed_buy_link.netloc:
                        query_params = parse_qs(parsed_buy_link.query)
                        if 'link' in query_params and query_params['link']:
                            target_url = query_params['link'][0]
                    parsed_target_url = urlparse(target_url)
                    hostname = parsed_target_url.netloc.lower()
                    if 'amazon.in' in hostname or 'amazon.com' in hostname: seller_name = 'Amazon'
                    elif 'flipkart.com' in hostname: seller_name = 'Flipkart'
                    elif 'croma.com' in hostname: seller_name = 'Croma'
                    elif 'jiomart.com' in hostname: seller_name = 'JioMart'
                    elif 'vijaysales.com' in hostname: seller_name = 'Vijay Sales'
                    elif 'reliancedigital.in' in hostname: seller_name = 'Reliance Digital'
                    elif 'tatacliq.com' in hostname: seller_name = 'Tata CLiQ'
                    elif 'shopclues.com' in hostname: seller_name = 'ShopClues'
                    elif 'paytmmall.com' in hostname: seller_name = 'Paytm Mall'
                except Exception:
                    pass

            # Append result if valid data found
            if price_str != "N/A" or seller_name != "N/A": # Basic check if we extracted *something* useful
                results.append({
                    "seller": seller_name,
                    "title": product_title,
                    "price": price_str,
                    "link": buy_link
                })

        return results

    except requests.exceptions.Timeout:
        print(f"❌ Timeout downloading BuyHatke page HTML: {tracker_url}")
        return None # Indicate download error
    except requests.exceptions.RequestException as e:
        print(f"❌ Error downloading BuyHatke page HTML: {e}")
        return None # Indicate download error
    except Exception as e:
        print(f"❌ An unexpected error occurred during scraping: {e}")
        import traceback
        traceback.print_exc()
        return None # Indicate scraping error

# --- Flask Routes ---

@app.route('/', methods=['GET', 'POST'])
def index():
    error = None
    product_info = None
    alternatives = None
    lowest_price_option = None
    site_type = None
    product_id = None
    tracker_url = None
    input_url = "" # Keep track of the submitted URL

    if request.method == 'POST':
        input_url = request.form.get('product_url', '').strip()
        if not input_url:
            error = "Please enter a product URL."
            # Pass input_url back even on immediate error
            return render_template('index.html', error=error, input_url=input_url)

        print(f"\nProcessing URL: {input_url}")

        # Detect Site and Extract ID
        original_domain = "N/A" # Default value
        try:
            parsed_url = urlparse(input_url)
            domain = parsed_url.netloc.lower()
            original_domain = domain # Store the extracted domain

            if "amazon." in domain:
                site_type = "amazon"
                product_id = extract_pid_amazon(input_url)
                id_type = "ASIN"
            elif "flipkart.com" in domain:
                site_type = "flipkart"
                product_id = extract_pid_flipkart(input_url)
                id_type = "PID"
            else:
                error = "URL does not appear to be a valid Amazon or Flipkart link."
                site_type = None # Prevent further processing

        except Exception as e:
            error = f"Error parsing input URL: {e}"
            site_type = None # Prevent further processing

        # Proceed if site detected and ID extracted
        if site_type and product_id:
            print(f"✅ Detected {site_type.capitalize()} URL. Extracted {id_type}: {product_id}")

            # Fetch initial details from BuyHatke API
            api_name, api_price, tracker_url, thumbnails = fetch_price_from_buyhatke(product_id, site_type)

            if api_name is not None or api_price is not None:
                price_display = 'N/A'
                original_price_numeric = None
                
                if api_price is not None:
                    try:
                        original_price_numeric = float(api_price)
                        price_display = f"₹{original_price_numeric:,.2f}"
                    except (ValueError, TypeError):
                        price_display = f"₹{api_price}"

                # Include the extracted domain in product_info
                product_info = {
                    "name": api_name or "N/A",
                    "price": price_display,
                    "price_numeric": original_price_numeric,
                    "tracker_url": tracker_url,
                    "original_url": input_url,
                    "original_domain": original_domain,
                    "site_type": site_type,
                    "thumbnails": thumbnails
                }

                if tracker_url:
                    # Scrape alternatives
                    alternatives = scrape_buyhatke_alternatives(tracker_url)
                    if alternatives is None:
                        error = "Could not fetch alternative prices (scraping error)."
                        alternatives = []
                    elif not alternatives:
                        print("ℹ️ No alternative prices found on the tracker page.")
                    else:
                        # Find the lowest price option among all options (including original)
                        lowest_price = float('inf')
                        lowest_price_item = None
                        
                        # Process price strings to extract numeric values for comparison
                        for item in alternatives:
                            if item['price'] != 'N/A':
                                # Extract numeric price value using regex
                                price_match = re.search(r'₹\s*([\d,]+(?:\.\d+)?)', item['price'])
                                if price_match:
                                    # Remove commas and convert to float
                                    try:
                                        price_val = float(price_match.group(1).replace(',', ''))
                                        item['price_numeric'] = price_val
                                        
                                        if price_val < lowest_price:
                                            lowest_price = price_val
                                            lowest_price_item = item
                                    except (ValueError, TypeError):
                                        continue
                        
                        # Compare with original price
                        if original_price_numeric is not None:
                            if original_price_numeric < lowest_price:
                                lowest_price = original_price_numeric
                                lowest_price_item = {
                                    'seller': site_type.capitalize(),
                                    'price': price_display,
                                    'price_numeric': original_price_numeric,
                                    'link': input_url,
                                    'is_original': True
                                }
                            
                        # Set the lowest price option
                        if lowest_price_item:
                            lowest_price_option = lowest_price_item
                            print(f"✅ Lowest price found: {lowest_price_item['seller']} - {lowest_price_item['price']}")
                else:
                    error = "Could not construct BuyHatke tracker URL. Cannot fetch alternatives."
                    alternatives = [] # Ensure alternatives is iterable

            else:
                error = f"Failed to fetch initial details for {product_id} from BuyHatke API. Product might not be tracked or API issue."
                # Pass back basic info even on failure
                product_info = {
                    "original_url": input_url,
                    "original_domain": original_domain
                }
                alternatives = [] # Ensure alternatives is iterable

        elif site_type and not product_id:
            error = f"Could not extract Product ID ({'ASIN' if site_type == 'amazon' else 'PID'}) from the {site_type.capitalize()} URL. Please check the link."
        elif not site_type and not error: # URL was invalid or unsupported
            if not error: # If no specific error set yet
                error = "Could not process the provided URL. Please ensure it's a valid Amazon or Flipkart product link."

        # Render the template with results or errors
        return render_template('index.html',
                              error=error,
                              product_info=product_info,
                              alternatives=alternatives,
                              lowest_price_option=lowest_price_option,
                              input_url=input_url) # Pass URL back to prefill input

    # For GET requests
    return render_template('index.html', input_url=input_url) # Pass empty input_url initially

# Make sure urlparse is imported at the top
# --- Run the App ---
if __name__ == '__main__':
    # Use 'waitress' for a production-ready WSGI server if deploying
    # from waitress import serve
    # serve(app, host='0.0.0.0', port=5000)
    # For development:
    app.run(debug=True) # debug=True enables auto-reload and detailed error pages