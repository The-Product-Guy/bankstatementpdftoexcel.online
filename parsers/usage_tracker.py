#!/usr/bin/env python3
"""
Usage Tracking and Rate Limiting for SaaS Model
Supports both pay-per-document and subscription models
"""
import os
import json
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from dataclasses import dataclass, asdict
from functools import wraps
import hashlib

# Storage backends (can swap to Redis for production)
_usage_store: Dict[str, Any] = {}


@dataclass
class UsageRecord:
    """Record of a single document processing."""
    user_id: str
    document_id: str
    pages_processed: int
    transactions_extracted: int
    llm_tokens_used: int
    estimated_cost_usd: float
    processing_time_seconds: float
    timestamp: str
    bank_type: str
    success: bool


@dataclass
class UserPlan:
    """User subscription plan configuration."""
    plan_type: str  # 'free', 'monthly', 'yearly', 'pay_per_doc'
    documents_per_month: int
    pages_per_document: int
    expires_at: Optional[str]  # ISO datetime or None for pay-per-doc
    price_usd: float
    
    # Usage counters (reset monthly for subscriptions)
    documents_used: int = 0
    pages_used: int = 0
    last_reset: Optional[str] = None


# Default plan configurations
PLANS = {
    'free': UserPlan(
        plan_type='free',
        documents_per_month=5,
        pages_per_document=10,
        expires_at=None,
        price_usd=0.0
    ),
    'starter': UserPlan(
        plan_type='monthly',
        documents_per_month=50,
        pages_per_document=50,
        expires_at=None,
        price_usd=9.99
    ),
    'professional': UserPlan(
        plan_type='monthly',
        documents_per_month=200,
        pages_per_document=100,
        expires_at=None,
        price_usd=29.99
    ),
    'business': UserPlan(
        plan_type='yearly',
        documents_per_month=1000,
        pages_per_document=200,
        expires_at=None,
        price_usd=249.99  # per year
    ),
    'pay_per_doc': UserPlan(
        plan_type='pay_per_doc',
        documents_per_month=999999,  # Unlimited
        pages_per_document=500,
        expires_at=None,
        price_usd=0.0  # Charged per document
    )
}

# Pay-per-document pricing
PAY_PER_DOC_PRICING = {
    'base_price': 0.10,      # Base price per document
    'per_page': 0.02,        # Additional per page beyond first 5
    'free_pages': 5,         # Pages included in base price
    'llm_markup': 1.5        # Markup on LLM API costs
}


class UsageTracker:
    """
    Track and limit usage for SaaS billing.
    
    For production, replace the in-memory store with Redis:
    - pip install redis
    - Set REDIS_URL environment variable
    """
    
    def __init__(self):
        self._store = _usage_store
        self._redis = None
        self._init_storage()
    
    def _init_storage(self):
        """Initialize Redis if available, otherwise use in-memory."""
        redis_url = os.environ.get('REDIS_URL')
        if redis_url:
            try:
                import redis
                self._redis = redis.from_url(redis_url)
                self._redis.ping()
                print("✅ Connected to Redis for usage tracking")
            except Exception as e:
                print(f"⚠️ Redis unavailable: {e}. Using in-memory storage.")
                self._redis = None
    
    def _get_key(self, prefix: str, user_id: str) -> str:
        """Generate storage key."""
        return f"{prefix}:{user_id}"
    
    def _get(self, key: str) -> Optional[str]:
        """Get value from storage."""
        if self._redis:
            return self._redis.get(key)
        return self._store.get(key)
    
    def _set(self, key: str, value: str, expires_seconds: Optional[int] = None):
        """Set value in storage."""
        if self._redis:
            if expires_seconds:
                self._redis.setex(key, expires_seconds, value)
            else:
                self._redis.set(key, value)
        else:
            self._store[key] = value
    
    def get_user_plan(self, user_id: str) -> UserPlan:
        """Get user's current plan (defaults to free)."""
        key = self._get_key("plan", user_id)
        data = self._get(key)
        
        if data:
            try:
                plan_data = json.loads(data)
                return UserPlan(**plan_data)
            except Exception:
                pass
        
        # Return copy of free plan for new users
        return UserPlan(**asdict(PLANS['free']))
    
    def set_user_plan(self, user_id: str, plan_name: str) -> UserPlan:
        """Set user's plan."""
        if plan_name not in PLANS:
            raise ValueError(f"Unknown plan: {plan_name}")
        
        plan = UserPlan(**asdict(PLANS[plan_name]))
        
        # Set expiry for subscription plans
        if plan.plan_type == 'monthly':
            expires_at = (datetime.now() + timedelta(days=30)).isoformat()
            plan.expires_at = expires_at
        elif plan.plan_type == 'yearly':
            expires_at = (datetime.now() + timedelta(days=365)).isoformat()
            plan.expires_at = expires_at
        
        plan.last_reset = datetime.now().isoformat()
        
        key = self._get_key("plan", user_id)
        self._set(key, json.dumps(asdict(plan)))
        
        return plan
    
    def check_can_process(
        self, 
        user_id: str, 
        page_count: int
    ) -> Tuple[bool, str, Optional[float]]:
        """
        Check if user can process a document.
        
        Args:
            user_id: The user's ID
            page_count: Number of pages in the document
            
        Returns:
            Tuple of (can_process, reason_if_not, estimated_cost)
        """
        plan = self.get_user_plan(user_id)
        
        # Check monthly reset
        plan = self._check_monthly_reset(user_id, plan)
        
        # Check subscription expiry
        if plan.expires_at:
            expires = datetime.fromisoformat(plan.expires_at)
            if datetime.now() > expires:
                return False, "Your subscription has expired. Please renew.", None
        
        # Check page limit per document
        if page_count > plan.pages_per_document:
            return (
                False, 
                f"Document has {page_count} pages but your plan allows max {plan.pages_per_document} per document.",
                None
            )
        
        # Check document limit (for subscription plans)
        if plan.plan_type != 'pay_per_doc':
            if plan.documents_used >= plan.documents_per_month:
                return (
                    False,
                    f"You've used all {plan.documents_per_month} documents for this month. "
                    f"Upgrade or wait until next billing cycle.",
                    None
                )
        
        # Calculate estimated cost
        if plan.plan_type == 'pay_per_doc':
            cost = self._calculate_document_cost(page_count)
        else:
            cost = 0.0  # Included in subscription
        
        return True, "OK", cost
    
    def _calculate_document_cost(self, page_count: int) -> float:
        """Calculate cost for pay-per-document model."""
        pricing = PAY_PER_DOC_PRICING
        
        cost = pricing['base_price']
        
        # Add per-page cost for pages beyond free tier
        extra_pages = max(0, page_count - pricing['free_pages'])
        cost += extra_pages * pricing['per_page']
        
        return round(cost, 2)
    
    def record_usage(
        self,
        user_id: str,
        document_id: str,
        pages: int,
        transactions: int,
        tokens: int,
        api_cost: float,
        processing_time: float,
        bank_type: str,
        success: bool
    ) -> UsageRecord:
        """
        Record a document processing event.
        
        Args:
            user_id: User who processed the document
            document_id: Unique document identifier
            pages: Number of pages processed
            transactions: Number of transactions extracted
            tokens: LLM tokens used
            api_cost: Estimated API cost
            processing_time: Processing time in seconds
            bank_type: Type of bank statement
            success: Whether processing succeeded
            
        Returns:
            The created UsageRecord
        """
        record = UsageRecord(
            user_id=user_id,
            document_id=document_id,
            pages_processed=pages,
            transactions_extracted=transactions,
            llm_tokens_used=tokens,
            estimated_cost_usd=api_cost,
            processing_time_seconds=processing_time,
            timestamp=datetime.now().isoformat(),
            bank_type=bank_type,
            success=success
        )
        
        # Store the record
        record_key = self._get_key("usage", f"{user_id}:{document_id}")
        self._set(record_key, json.dumps(asdict(record)))
        
        # Update plan usage counters
        if success:
            plan = self.get_user_plan(user_id)
            plan.documents_used += 1
            plan.pages_used += pages
            
            plan_key = self._get_key("plan", user_id)
            self._set(plan_key, json.dumps(asdict(plan)))
        
        return record
    
    def _check_monthly_reset(self, user_id: str, plan: UserPlan) -> UserPlan:
        """Check if monthly usage counters should be reset."""
        if plan.plan_type == 'pay_per_doc':
            return plan  # No monthly reset for pay-per-doc
        
        if not plan.last_reset:
            plan.last_reset = datetime.now().isoformat()
            return plan
        
        last_reset = datetime.fromisoformat(plan.last_reset)
        now = datetime.now()
        
        # Reset if more than 30 days since last reset
        if (now - last_reset) > timedelta(days=30):
            plan.documents_used = 0
            plan.pages_used = 0
            plan.last_reset = now.isoformat()
            
            # Save updated plan
            key = self._get_key("plan", user_id)
            self._set(key, json.dumps(asdict(plan)))
        
        return plan
    
    def get_usage_summary(self, user_id: str) -> Dict[str, Any]:
        """Get usage summary for a user."""
        plan = self.get_user_plan(user_id)
        
        return {
            'plan_type': plan.plan_type,
            'documents_used': plan.documents_used,
            'documents_limit': plan.documents_per_month,
            'documents_remaining': max(0, plan.documents_per_month - plan.documents_used),
            'pages_used': plan.pages_used,
            'max_pages_per_doc': plan.pages_per_document,
            'expires_at': plan.expires_at,
            'price': plan.price_usd
        }


# Global tracker instance
_tracker: Optional[UsageTracker] = None


def get_usage_tracker() -> UsageTracker:
    """Get or create the global usage tracker."""
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker()
    return _tracker


def generate_document_id(filename: str, user_id: str) -> str:
    """Generate a unique document ID."""
    timestamp = datetime.now().isoformat()
    combined = f"{user_id}:{filename}:{timestamp}"
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


# Flask decorator for rate limiting
def require_usage_quota(f):
    """
    Decorator to check usage quota before processing.
    Use with Flask routes that process documents.
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        from flask import request, jsonify, session
        
        # Get user ID (from session, API key, or IP)
        user_id = session.get('user_id') or request.headers.get('X-API-Key') or request.remote_addr
        
        # Get page count from request (needs to be set in the route)
        page_count = getattr(request, '_page_count', 1)
        
        tracker = get_usage_tracker()
        can_process, message, _ = tracker.check_can_process(user_id, page_count)
        
        if not can_process:
            return jsonify({
                'error': 'Usage limit exceeded',
                'message': message,
                'upgrade_url': '/pricing'
            }), 429
        
        return f(*args, **kwargs)
    
    return decorated_function
