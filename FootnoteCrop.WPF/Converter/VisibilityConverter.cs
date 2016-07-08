using System;
using System.Globalization;
using System.Windows;
using Nalarium;
using IValueConverter = System.Windows.Data.IValueConverter;

namespace FootnoteCrop.WPF.Converter
{
    public class VisibilityConverter : IValueConverter
    {
        public object Convert(object value, Type targetType, object parameter, CultureInfo culture)
        {
            return Parser.ParseBoolean(value) ? Visibility.Visible : Visibility.Hidden;
        }

        public object ConvertBack(object value, Type targetType, object parameter, CultureInfo culture)
        {
            throw new NotImplementedException();
        }
    }
}