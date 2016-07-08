using System;
using System.Globalization;
using System.Windows.Data;

namespace FootnoteCrop.WPF.Converter
{
    public class PageNumberValueConverter : IValueConverter
    {
        public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
        {
            if (value == null)
            {
                return 0;
            }
            return "Page " + value;
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        {
            throw new NotImplementedException();
        }
    }
}