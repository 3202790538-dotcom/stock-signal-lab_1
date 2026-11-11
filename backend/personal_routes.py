"""Local-only personal planning routes; this module never sends broker orders."""
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from personal_account import (
    PersonalSettings, ManualAccountInput, load_settings, save_settings,
    load_manual_account, save_manual_account, selected_holdings,
)
from personal_plan import build_personal_plan


def personal_router(data_dir, read_market, read_watchlists, market_issue, daily_status):
    router = APIRouter()

    @router.get('/api/personal-settings')
    def settings():
        return load_settings(data_dir())

    @router.put('/api/personal-settings')
    def update_settings(body: PersonalSettings):
        return save_settings(data_dir(), body.model_dump())

    @router.get('/api/manual-account')
    def manual_account():
        return load_manual_account(data_dir())

    @router.put('/api/manual-account')
    def update_manual_account(body: ManualAccountInput):
        return save_manual_account(data_dir(), body.model_dump())

    @router.post('/api/personal-plan')
    def personal_plan(body: PersonalSettings):
        settings = body.model_dump()
        market = read_market()
        try:
            holdings = selected_holdings(data_dir(), market, settings)
            return build_personal_plan(market, holdings, read_watchlists(), settings,
                                       market_issue=market_issue())
        except (ValueError, TypeError) as exc:
            return JSONResponse({'error': str(exc)}, status_code=422)

    @router.get('/api/daily-sync-status')
    def sync_status():
        return daily_status()

    return router
