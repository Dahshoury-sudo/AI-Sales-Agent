import threading
import requests
import time

URL = "http://localhost:8000/api/chat/"
HEADERS = {
    "X-API-Key": "FUe_KGAxyiN1d8ZNO4S1-2geJR50T4k3iB_UKnMFvqA", # Actual Store API Key
}

def send_request(thread_id):
    start_time = time.time()
    print(f"[Thread {thread_id}] Sending request...")
    try:
        response = requests.post(
            URL, 
            json={"message": "رشحلي عطر مناسب للخروجات بليل"}, 
            headers=HEADERS
        )
        end_time = time.time()
        print(f"[Thread {thread_id}] Received response in {end_time - start_time:.2f} seconds!")
    except Exception as e:
        print(f"[Thread {thread_id}] Failed: {e}")

if __name__ == "__main__":
    print("Starting concurrency test...")
    threads = []
    
    # Launch 5 requests at the exact same time
    for i in range(5):
        t = threading.Thread(target=send_request, args=(i+1,))
        threads.append(t)
        t.start()
        
    for t in threads:
        t.join()
        
    print("All requests completed!")
