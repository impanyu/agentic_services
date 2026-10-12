"""First-party App aliases, without changing legacy Checkout return URLs."""
APP_SERVICES=('photo-scout','web-evidence','niche-discovery','contractor-check')
APP_ORIGINS=tuple('https://'+service+'.aisoup.net' for service in APP_SERVICES)

def app_web_home(request,service):
    if service not in APP_SERVICES:raise ValueError('Unknown App')
    host=service+'.aisoup.net'
    if request.headers.get('x-forwarded-host','').lower()==host:return 'https://'+host+'/'
    return 'https://aisoup.net/'+service+'/'
