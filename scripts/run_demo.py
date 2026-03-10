import time
import requests

def run_demo():
    print("🚀 ADRC Demo Script Started")
    url = "http://localhost:8000/webhook/sms"
    confirm_url = "http://localhost:8000/webhook/sms/confirm"

    messages = [
        {
            "From": "+919000000001",
            "Body": "Massive flood in Connaught Place! We need help!"
        },
        {
            "From": "+919000000002",
            "Body": "Water rising fast, people trapped in Connaught place."
        },
        {
            "From": "+919000000003",
            "Body": "Need boats in Connaught place, heavy flooding everywhere!"
        }
    ]

    for msg in messages:
        print(f"[{time.strftime('%H:%M:%S')}] Simulating SMS from {msg['From']}...")
        try:
            requests.post(url, data=msg)
        except requests.exceptions.ConnectionError:
            print("❌ Failed to connect to localhost:8000. Is the backend running?")
            return
        time.sleep(1)

    print("✅ Sent 3 reports. Cluster should be PENDING_VERIFICATION.")
    print("⏳ Waiting 3 seconds before confirming...")
    time.sleep(3)

    print(f"[{time.strftime('%H:%M:%S')}] Simulating L3 node (+919810000001) confirmation...")
    requests.post(confirm_url, data={"From": "+919810000001", "Body": "YES"})
    
    print("🎉 Done! Crisis should now be ACTIVE on the dashboard.")

if __name__ == "__main__":
    run_demo()
