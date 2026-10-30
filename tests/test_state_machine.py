from app.models import PaymentStatus
from app.services.payment import ACTIVE_PAYMENT_STATES


def test_terminal_payment_states_are_not_active():
    assert PaymentStatus.SUCCESS not in ACTIVE_PAYMENT_STATES
    assert PaymentStatus.FAILED not in ACTIVE_PAYMENT_STATES
    assert PaymentStatus.CANCELLED not in ACTIVE_PAYMENT_STATES


def test_provider_waiting_state_is_active():
    assert PaymentStatus.WAITING_FOR_PROVIDER in ACTIVE_PAYMENT_STATES
