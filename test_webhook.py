import requests

def test_webhook(url):
    """Tests the webhook URL using the requests library."""
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()  # Raise an exception for bad status codes
        print(f"Success! Status code: {response.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"Error: {e}")

if __name__ == '__main__':
    webhook_url = "https://webhook.skycracker.com.br/webhook/fbf031f4-c238-4a58-b1a7-2c4ca2d09161"
    print(f"Testing webhook URL: {webhook_url}")
    test_webhook(webhook_url)
