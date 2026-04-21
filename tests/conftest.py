import os

os.environ.update(
    {
        "ENV": "test",
        "FRONTEND_URL": "http://localhost:3000",
        "GOOGLE_APPLICATION_CREDENTIALS": "fake.json",
        "STRIPE_SECRET_KEY": "sk_test_fake",
        "RESEND_API_KEY": "re_fake",
        "RESEND_EMAIL": "test@example.com",
        "REDIS_HOST": "localhost",
        "REDIS_PORT": "6379",
        "REDIS_DB": "0",
        "REDIS_KEY_PREFIX": "test:",
        "CACHE_TTL_MEDIUM": "300",
        "FREE_KEY_PREFIX": "free:",
        "PREMIUM_KEY_PREFIX": "premium:",
    }
)
