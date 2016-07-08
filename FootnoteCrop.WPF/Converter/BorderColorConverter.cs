using System;
using System.Globalization;
using System.Windows.Data;
using System.Windows.Media;

namespace FootnoteCrop.WPF.Converter
{
    public class BorderColorConverter : IValueConverter
    {
        public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
        {
            if (value == null)
            {
                return new SolidColorBrush(Colors.Black);
            }
            var valueBoolean = (bool) value;
            return valueBoolean ? new SolidColorBrush(Colors.Red) : new SolidColorBrush(Colors.Green);
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        {
            throw new NotImplementedException();
        }
    }
}