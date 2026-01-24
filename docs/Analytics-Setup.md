# Analytics Setup Guide

## Google Analytics (gtag.js) Configuration

The application now supports Google Analytics via gtag.js. This is the primary analytics solution that has been integrated.

### Setup Instructions

1. **Get Your Google Analytics Measurement ID**
   - Go to [Google Analytics](https://analytics.google.com/)
   - Create a property for your website
   - Copy your Measurement ID (format: `G-XXXXXXXXXX`)

2. **Set Environment Variable**

   **For Local Development (.env file):**
   ```env
   GA_MEASUREMENT_ID=G-XXXXXXXXXX
   ```

   **For Production (Railway/Heroku/etc.):**
   - Set the environment variable `GA_MEASUREMENT_ID` in your deployment platform
   - Value: Your Google Analytics Measurement ID (e.g., `G-ABC123DEF4`)

3. **Verify Installation**
   - Deploy your application
   - Visit your website
   - Open browser DevTools → Network tab
   - Look for requests to `googletagmanager.com/gtag/js?id=G-XXXXXXXXXX` (with your actual ID)
   - Check Google Analytics Real-Time reports to see if visits are being tracked

## Google Tag Manager (Optional)

If you also want to use Google Tag Manager, you can set both:

```env
GA_MEASUREMENT_ID=G-XXXXXXXXXX
GTM_CONTAINER_ID=GTM-XXXXXXX
```

Both will work together - Google Analytics will track page views, and GTM can be used for additional tags and event tracking.

## Event Tracking

You can track custom events in your JavaScript code:

```javascript
// Track file upload
gtag('event', 'file_upload', {
  'event_category': 'conversion',
  'event_label': 'pdf_upload'
});

// Track conversion completion
gtag('event', 'conversion_complete', {
  'event_category': 'conversion',
  'event_label': 'excel_download'
});
```

## Recommended Events to Track

1. **File Upload** - When user uploads a PDF
2. **Conversion Start** - When processing begins
3. **Conversion Complete** - When Excel file is ready
4. **Download** - When user downloads the file
5. **Error** - When conversion fails

## Implementation Status

✅ Google Analytics (gtag.js) - Integrated  
✅ Google Tag Manager - Optional, integrated if configured  
✅ Environment variable configuration - Ready  
✅ Base template integration - Complete  

## Testing

1. Set `GA_MEASUREMENT_ID=your-measurement-id` in your environment
2. Deploy or run locally
3. Visit your website
4. Check Google Analytics Real-Time reports
5. Verify page views are being tracked

## Notes

- Google Analytics will only load if `GA_MEASUREMENT_ID` is set
- Both GA and GTM can be used simultaneously
- All pages automatically include the tracking code via base template
- No additional configuration needed once environment variable is set

---

**Last Updated**: January 27, 2025
