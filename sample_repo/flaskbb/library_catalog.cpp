#include <iostream>
#include <string>

class LibraryItem {
protected:
    std::string title;
    int id;

public:
    LibraryItem(const std::string& t, int i) : title(t), id(i) {}

protected:
    virtual std::string describe() {
        return "ID: " + std::to_string(id) + ", Title: " + title;
    }
};

class Book : public LibraryItem {
private:
    std::string author;
    int pages;

public:
    Book(const std::string& t, int i, const std::string& a, int p)
        : LibraryItem(t, i), author(a), pages(p) {}

    void display() {
        std::cout << describe() << std::endl;
        std::cout << "Author: " << author << std::endl;
        std::cout << "Pages: " << pages << std::endl;
    }
};

class Magazine : public LibraryItem {
private:
    int issueNumber;
    std::string month;

public:
    Magazine(const std::string& t, int i, int n, const std::string& m)
        : LibraryItem(t, i), issueNumber(n), month(m) {}

    void display() {
        std::cout << describe() << std::endl;
        std::cout << "Issue Number: " << issueNumber << std::endl;
        std::cout << "Month: " << month << std::endl;
    }
};

int main() {
    Book hobbity(Hobbit, 3, "J.R.R. Tolkien", 310);
    Magazine natgeo(National Geographic, 100, 5, "May");

    std::cout << "Book Details:" << std::endl;
    hobbity.display();

    std::cout << "\nMagazine Details:" << std::endl;
    natgeo.display();

    return 0;
}
